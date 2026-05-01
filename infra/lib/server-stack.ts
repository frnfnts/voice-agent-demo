import * as path from 'path';
import { Stack, StackProps, CfnOutput, CustomResource, Duration, Tags, Fn } from 'aws-cdk-lib';
import { Construct } from 'constructs';
import * as ec2 from 'aws-cdk-lib/aws-ec2';
import * as iam from 'aws-cdk-lib/aws-iam';
import * as cloudfront from 'aws-cdk-lib/aws-cloudfront';
import * as origins from 'aws-cdk-lib/aws-cloudfront-origins';
import * as lambda from 'aws-cdk-lib/aws-lambda';
import * as cr from 'aws-cdk-lib/custom-resources';
import { DockerImageAsset, Platform } from 'aws-cdk-lib/aws-ecr-assets';

export class ServerStack extends Stack {
  public readonly serverUrl: string;

  constructor(scope: Construct, id: string, props?: StackProps) {
    super(scope, id, props);

    // ── DockerImageAsset: cdk deploy 時に自動ビルド & CDK マネージド ECR へプッシュ ──
    const image = new DockerImageAsset(this, 'ServerImage', {
      directory: path.join(__dirname, '../../python-server'),
      platform: Platform.LINUX_ARM64,
    });

    // ── IAM ──
    const instanceRole = new iam.Role(this, 'Ec2Role', {
      assumedBy: new iam.ServicePrincipal('ec2.amazonaws.com'),
      managedPolicies: [
        // SSM Session Manager (SSH の代替)
        iam.ManagedPolicy.fromAwsManagedPolicyName('AmazonSSMManagedInstanceCore'),
      ],
    });
    // CDK マネージド ECR からの pull 権限
    image.repository.grantPull(instanceRole);
    instanceRole.addToPolicy(new iam.PolicyStatement({
      actions: ['ssm:GetParameter'],
      resources: [
        `arn:aws:ssm:${this.region}:${this.account}:parameter/openai-key`,
        `arn:aws:ssm:${this.region}:${this.account}:parameter/recallai-key`,
        `arn:aws:ssm:${this.region}:${this.account}:parameter/google-drive-credential`,
      ],
    }));

    // ── VPC / Security Group ──
    const vpc = ec2.Vpc.fromLookup(this, 'DefaultVpc', { isDefault: true });
    const sg = new ec2.SecurityGroup(this, 'Sg', { vpc, allowAllOutbound: true });

    // CloudFront からのみポート 3000 を受け付ける (SSH:22 は開放しない)
    sg.addIngressRule(
      ec2.Peer.prefixList('pl-58a04531'), // ap-northeast-1 CloudFront origin-facing IP
      ec2.Port.tcp(3000),
      'From CloudFront only',
    );

    // ── EC2 User Data (初回起動のみ: Docker インストール & デプロイスクリプト配置) ──
    const ecrDomain = `${this.account}.dkr.ecr.${this.region}.amazonaws.com`;
    const userData = ec2.UserData.forLinux();
    userData.addCommands(
      'dnf update -y',
      'dnf install -y docker',
      'systemctl enable --now docker',

      // deploy.sh: Custom Resource の SSM RunCommand から呼び出す
      `cat > /usr/local/bin/deploy.sh << 'DEPLOY'`,
      '#!/bin/bash',
      'set -e',
      'IMAGE_URI="$1"',
      `aws ecr get-login-password --region ${this.region} | docker login --username AWS --password-stdin ${ecrDomain}`,
      'docker pull "$IMAGE_URI"',
      'docker stop ai-interviewer 2>/dev/null || true',
      'docker rm   ai-interviewer 2>/dev/null || true',
      'docker run -d \\',
      '  --name ai-interviewer \\',
      '  --restart always \\',
      '  -p 3000:3000 \\',
      `  -e AWS_REGION=${this.region} \\`,
      '  "$IMAGE_URI"',
      'DEPLOY',
      'chmod +x /usr/local/bin/deploy.sh',
    );

    // ── EC2 Instance (ARM64) ──
    const instance = new ec2.Instance(this, 'Instance', {
      vpc,
      vpcSubnets: { subnetType: ec2.SubnetType.PUBLIC },
      instanceType: ec2.InstanceType.of(ec2.InstanceClass.T4G, ec2.InstanceSize.MICRO),
      machineImage: ec2.MachineImage.latestAmazonLinux2023({
        cpuType: ec2.AmazonLinuxCpuType.ARM_64,
      }),
      role: instanceRole,
      securityGroup: sg,
      userData,
      requireImdsv2: true,
    });
    Tags.of(instance).add('Name', 'ai-interviewer');

    // ── Elastic IP ──
    const eip = new ec2.CfnEIP(this, 'Eip', { instanceId: instance.instanceId });
    const eipDns = Fn.sub('ec2-${IpWithDashes}.${AWS::Region}.compute.amazonaws.com', {
      IpWithDashes: Fn.join('-', Fn.split('.', eip.attrPublicIp)),
    });

    // ── CloudFront (WebSocket 対応) ──
    const distribution = new cloudfront.Distribution(this, 'Distribution', {
      defaultBehavior: {
        origin: new origins.HttpOrigin(eipDns, {
          httpPort: 3000,
          protocolPolicy: cloudfront.OriginProtocolPolicy.HTTP_ONLY,
        }),
        allowedMethods: cloudfront.AllowedMethods.ALLOW_ALL,
        cachePolicy: cloudfront.CachePolicy.CACHING_DISABLED,
        originRequestPolicy: cloudfront.OriginRequestPolicy.ALL_VIEWER,
        viewerProtocolPolicy: cloudfront.ViewerProtocolPolicy.REDIRECT_TO_HTTPS,
      },
      comment: 'ai-interviewer python server',
    });

    this.serverUrl = `https://${distribution.distributionDomainName}`;

    // ── Custom Resource: cdk deploy のたびに imageUri が変われば SSM RunCommand で EC2 更新 ──
    const deployFn = new lambda.Function(this, 'DeployFn', {
      runtime: lambda.Runtime.PYTHON_3_12,
      handler: 'index.handler',
      timeout: Duration.minutes(15),
      code: lambda.Code.fromInline(`
import boto3, time

def handler(event, context):
    if event['RequestType'] == 'Delete':
        return {}
    instance_id = event['ResourceProperties']['InstanceId']
    image_uri   = event['ResourceProperties']['ImageUri']
    ssm = boto3.client('ssm')

    # User Data の完了を待つ（deploy.sh が配置されるまで最大10分リトライ）
    for _ in range(120):
        time.sleep(5)
        try:
            resp = ssm.send_command(
                InstanceIds=[instance_id],
                DocumentName='AWS-RunShellScript',
                Parameters={'commands': ['test -f /usr/local/bin/deploy.sh && echo ready']},
            )
            command_id = resp['Command']['CommandId']
            time.sleep(5)
            result = ssm.get_command_invocation(CommandId=command_id, InstanceId=instance_id)
            if result['Status'] == 'Success' and 'ready' in result.get('StandardOutputContent', ''):
                break
        except Exception:
            pass
    else:
        raise Exception('Timed out waiting for deploy.sh to be created by User Data')

    resp = ssm.send_command(
        InstanceIds=[instance_id],
        DocumentName='AWS-RunShellScript',
        Parameters={'commands': [f'/usr/local/bin/deploy.sh {image_uri}']},
    )
    command_id = resp['Command']['CommandId']

    # 完了を待つ（最大4分）
    for _ in range(48):
        time.sleep(5)
        result = ssm.get_command_invocation(CommandId=command_id, InstanceId=instance_id)
        status = result['Status']
        if status == 'Success':
            return {'CommandId': command_id}
        if status in ('Failed', 'Cancelled', 'TimedOut'):
            raise Exception(f'deploy.sh failed: {status}\\n{result.get("StandardErrorContent", "")}')
    raise Exception('deploy.sh timed out')
`),
    });

    deployFn.addToRolePolicy(new iam.PolicyStatement({
      actions: ['ssm:SendCommand', 'ssm:GetCommandInvocation'],
      resources: ['*'],
    }));

    const deployResource = new CustomResource(this, 'DeployTrigger', {
      serviceToken: new cr.Provider(this, 'DeployProvider', {
        onEventHandler: deployFn,
      }).serviceToken,
      properties: {
        InstanceId: instance.instanceId,
        ImageUri: image.imageUri, // URI が変わるたびに Custom Resource が再実行
      },
    });
    deployResource.node.addDependency(instance);

    new CfnOutput(this, 'ServerUrl', {
      value: this.serverUrl,
      description: 'Python server URL (wss:// も同ドメイン)',
    });
    new CfnOutput(this, 'InstanceId', { value: instance.instanceId });
  }
}
