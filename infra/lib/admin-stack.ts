import { Stack, StackProps, Duration, CfnOutput } from 'aws-cdk-lib';
import { Construct } from 'constructs';
import * as lambda from 'aws-cdk-lib/aws-lambda';
import * as iam from 'aws-cdk-lib/aws-iam';
import * as path from 'path';

interface AdminStackProps extends StackProps {
  frontendUrl: string;
  serverUrl: string;
}

export class AdminStack extends Stack {
  constructor(scope: Construct, id: string, props: AdminStackProps) {
    super(scope, id, props);

    const fn = new lambda.Function(this, 'AdminFn', {
      functionName: 'ai-interviewer-admin', // URL 固定のため名前を明示
      runtime: lambda.Runtime.PYTHON_3_11,
      handler: 'index.handler',
      code: lambda.Code.fromAsset(path.join(__dirname, '../../lambda/admin')),
      timeout: Duration.seconds(30),
      reservedConcurrentExecutions: 5, // DoS 対策: 同時実行数を制限
      environment: {
        RECALL_API_BASE:  'https://ap-northeast-1.recall.ai/api/v1',
        FRONTEND_URL:     props.frontendUrl,
        SERVER_URL:       props.serverUrl,
        AWS_PARAM_REGION: 'ap-northeast-1',
      },
    });

    fn.addToRolePolicy(new iam.PolicyStatement({
      actions: ['ssm:GetParameter'],
      resources: [
        `arn:aws:ssm:${this.region}:${this.account}:parameter/recallai-key`,
        `arn:aws:ssm:${this.region}:${this.account}:parameter/admin-token`,
      ],
    }));

    // Lambda Function URL — *.lambda-url.ap-northeast-1.on.aws で HTTPS 自動提供
    const fnUrl = fn.addFunctionUrl({
      authType: lambda.FunctionUrlAuthType.NONE, // ADMIN_TOKEN で独自認証
      cors: {
        allowedOrigins: ['*'],
        allowedMethods: [lambda.HttpMethod.GET, lambda.HttpMethod.POST],
      },
    });

    new CfnOutput(this, 'AdminUrl', {
      value: fnUrl.url,
      description: '管理 UI URL (非エンジニアに共有)',
    });
  }
}
