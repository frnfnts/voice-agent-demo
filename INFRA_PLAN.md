# AWS CDK インフラ構築プラン

## Context
ai-interviewer の Python バックエンド (aiohttp/WebSocket) を AWS 上にデプロイし、以下を実現する:
1. `cdk deploy` のみで完結するデプロイ (Docker ビルド・ECR プッシュ・EC2 コンテナ更新を一括)
2. 非エンジニアがブラウザで会議参加設定できる管理 Web UI — **Python サーバーとは完全に独立したリソース**
3. フロントエンドは既存の Cloudflare Pages 運用を継続 (CDK 管理外)

**制約: AWS リソースのみで完結 (外部 CA・外部ドメインレジストラ不使用)**

---

## アーキテクチャ概要

```
┌──────────────────────────────────────────────────────┐
│ フロントエンド: Cloudflare Pages (CDK 管理外)          │
│  develop push → 自動デプロイ (既存運用を継続)           │
│  URL: https://xxx.pages.dev                          │
└──────────────────────────────────────────────────────┘

┌──────────────────────────────────────────────────────┐
│ バックエンド: EC2 (t4g.micro) + CloudFront            │
│  HTTPS/WSS は CloudFront が終端                       │
│  EC2 へは CloudFront からのみアクセス可 (SG で制御)    │
│  URL: https://xxxx.cloudfront.net                    │
└──────────────────────────────────────────────────────┘

┌──────────────────────────────────────────────────────┐
│ 管理 UI: Lambda + Function URL  ← 完全独立            │
│  URL: https://xxxxx.lambda-url.ap-northeast-1.on.aws │
│  ※ cdk deploy による更新では URL は変化しない          │
└──────────────────────────────────────────────────────┘
```

---

## デプロイ方針

```bash
cd infra && npx cdk deploy --all
```

`cdk deploy` 1コマンドで以下がすべて実行される:

1. `DockerImageAsset` が python-server の Dockerfile をビルドし CDK マネージド ECR へプッシュ
2. CDK Custom Resource (Lambda) が SSM RunCommand で EC2 上のコンテナを更新
3. Lambda コードの変更も同時に反映

イメージの内容が変わらなければ Custom Resource は再実行されず、デプロイが高速になる。

> GitHub Actions・OIDC ロールは不要。

---

## ネットワークセキュリティ

| 対象 | 保護内容 |
|---|---|
| EC2 | Security Group で CloudFront からのみポート 3000 を許可。SSH ポート (22) は開放しない。EC2 操作は SSM Session Manager で行う |
| CloudFront | HTTPS/WSS を終端。EC2 への直接 HTTP アクセスは不可 |
| Lambda | HTTPS (Function URL)。トークン認証 + 同時実行数制限 |
| SSM | SecureString でシークレット管理。EC2 インスタンスロール / Lambda 実行ロールのみ取得権限 |

---

## コスト見積もり (ap-northeast-1)

| リソース | 月額 |
|---|---|
| EC2 t4g.micro (ARM64, 1GB RAM) | ~$7 |
| Elastic IP | $0 (インスタンス稼働中) |
| CloudFront (無料枠: 1TB/月) | ~$0 |
| Lambda + Function URL (無料枠: 100万req/月) | ~$0 |
| **合計** | **~$7/month** |

> ECR は `DockerImageAsset` が CDK マネージドリポジトリを自動管理するため別途作成不要。

---

## スタック構成

```
AiInterviewerServer      — EC2 + CloudFront (Python WebSocket サーバー)
AiInterviewerAdmin       — Lambda (管理 UI、Python サーバーと完全独立)
```

**廃止:**
- `AiInterviewerEcr` — DockerImageAsset が CDK マネージド ECR を自動管理するため不要
- `AiInterviewerGithubOidc` — GitHub Actions を使わないため不要
- GitHub Actions ワークフロー — `cdk deploy` に集約

---

## 作成・変更するファイル

```
ai-interviewer/
├── infra/
│   ├── bin/app.ts
│   └── lib/
│       ├── server-stack.ts         # EC2 + CloudFront + DockerImageAsset + Custom Resource
│       └── admin-stack.ts          # Lambda (管理 UI)
├── lambda/
│   └── admin/
│       └── index.py
└── python-server/
    ├── Dockerfile
    └── startup.py
```

---

## CDK スタック詳細

### 1. Server Stack (`infra/lib/server-stack.ts`)

`DockerImageAsset` でイメージをビルド & ECR プッシュし、CDK Custom Resource が SSM RunCommand で EC2 を更新する。イメージ URI が変わった場合のみ Custom Resource が再実行される。

```typescript
import * as path from 'path';
import { Stack, StackProps, CfnOutput, CustomResource, Duration } from 'aws-cdk-lib';
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
      actions: ['ssm:GetParameter', 'kms:Decrypt'],
      resources: [`arn:aws:ssm:${this.region}:${this.account}:parameter/ai-interviewer/*`],
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
      `aws ecr get-login-password --region ap-northeast-1 | docker login --username AWS --password-stdin $(echo $IMAGE_URI | cut -d/ -f1)`,
      'docker pull "$IMAGE_URI"',
      'docker stop ai-interviewer 2>/dev/null || true',
      'docker rm   ai-interviewer 2>/dev/null || true',
      'docker run -d \\',
      '  --name ai-interviewer \\',
      '  --restart always \\',
      '  -p 3000:3000 \\',
      '  -e AWS_REGION=ap-northeast-1 \\',
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

    // ── Elastic IP ──
    new ec2.CfnEIP(this, 'Eip', { instanceId: instance.instanceId });

    // ── CloudFront (WebSocket 対応) ──
    const distribution = new cloudfront.Distribution(this, 'Distribution', {
      defaultBehavior: {
        origin: new origins.HttpOrigin(instance.instancePublicDnsName, {
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

    // ── Custom Resource: cdk deploy のたびに SSM RunCommand で EC2 コンテナを更新 ──
    // imageUri が変わった場合のみ CloudFormation が再実行する
    const deployFn = new lambda.Function(this, 'DeployFn', {
      runtime: lambda.Runtime.PYTHON_3_12,
      handler: 'index.handler',
      timeout: Duration.minutes(5),
      code: lambda.Code.fromInline(`
import boto3, cfnresponse

def handler(event, context):
    if event['RequestType'] == 'Delete':
        cfnresponse.send(event, context, cfnresponse.SUCCESS, {})
        return
    try:
        instance_id = event['ResourceProperties']['InstanceId']
        image_uri   = event['ResourceProperties']['ImageUri']
        ssm = boto3.client('ssm')
        resp = ssm.send_command(
            InstanceIds=[instance_id],
            DocumentName='AWS-RunShellScript',
            Parameters={'commands': [f'/usr/local/bin/deploy.sh {image_uri}']},
        )
        cfnresponse.send(event, context, cfnresponse.SUCCESS,
                         {'CommandId': resp['Command']['CommandId']})
    except Exception as e:
        cfnresponse.send(event, context, cfnresponse.FAILED, {'Error': str(e)})
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
        ImageUri: image.imageUri,  // ← URI が変わるたびに Custom Resource が再実行
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
```

---

### 2. Admin Stack (`infra/lib/admin-stack.ts`)

Lambda + Function URL。Python サーバーとは完全に独立したスタック。

#### セキュリティ方針

| 対策 | 実装箇所 | 内容 |
|---|---|---|
| DoS 対策 | CDK `reservedConcurrentExecutions: 5` | 同時実行数を 5 に制限。超過リクエストは即 429 を返す |
| 認証 | Lambda コード内トークン検証 | SSM `/ai-interviewer/ADMIN_TOKEN` と照合。不一致は 401 を返す |

**トークンの運用**:
- `openssl rand -hex 32` で生成し SSM に登録
- 社内メンバーへは Slack DM 等で直接共有
- URL に `?token=<ADMIN_TOKEN>` を付けてブックマーク配布
- ローテーション時は SSM の値を更新して `cdk deploy AiInterviewerAdmin` のみ実行

#### Lambda URL の固定について

`functionName: 'ai-interviewer-admin'` を指定することで Function URL は安定する。
`cdk deploy` による更新 (コード変更・環境変数変更) では URL は変化しない。
URL が変わるのはスタックを `cdk destroy` して再作成した場合のみ。

```typescript
import { Stack, StackProps, Duration, CfnOutput } from 'aws-cdk-lib';
import { Construct } from 'constructs';
import * as lambda from 'aws-cdk-lib/aws-lambda';
import * as iam from 'aws-cdk-lib/aws-iam';

interface AdminStackProps extends StackProps {
  frontendUrl: string;
  serverUrl: string;
}

export class AdminStack extends Stack {
  constructor(scope: Construct, id: string, props: AdminStackProps) {
    super(scope, id, props);

    const fn = new lambda.Function(this, 'AdminFn', {
      functionName: 'ai-interviewer-admin',   // ← URL 固定のため名前を明示
      runtime: lambda.Runtime.PYTHON_3_11,
      handler: 'index.handler',
      code: lambda.Code.fromAsset('../lambda/admin'),
      timeout: Duration.seconds(30),
      reservedConcurrentExecutions: 5,        // DoS 対策
      environment: {
        RECALL_API_BASE: 'https://ap-northeast-1.recall.ai/api/v1',
        FRONTEND_URL:    props.frontendUrl,
        SERVER_URL:      props.serverUrl,
        AWS_PARAM_REGION: 'ap-northeast-1',
      },
    });

    fn.addToRolePolicy(new iam.PolicyStatement({
      actions: ['ssm:GetParameter', 'kms:Decrypt'],
      resources: [
        `arn:aws:ssm:${this.region}:${this.account}:parameter/ai-interviewer/RECALL_TOKEN`,
        `arn:aws:ssm:${this.region}:${this.account}:parameter/ai-interviewer/ADMIN_TOKEN`,
      ],
    }));

    const fnUrl = fn.addFunctionUrl({
      authType: lambda.FunctionUrlAuthType.NONE,  // ADMIN_TOKEN で独自認証
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
```

---

### 3. CDK エントリポイント (`infra/bin/app.ts`)

```typescript
import 'source-map-support/register';
import * as cdk from 'aws-cdk-lib';
import { ServerStack } from '../lib/server-stack';
import { AdminStack }  from '../lib/admin-stack';

const app = new cdk.App();
const env = { account: process.env.CDK_DEFAULT_ACCOUNT, region: 'ap-northeast-1' };

const server = new ServerStack(app, 'AiInterviewerServer', { env });

const admin = new AdminStack(app, 'AiInterviewerAdmin', {
  env,
  frontendUrl: 'https://your-project.pages.dev', // Cloudflare Pages URL
  serverUrl:   server.serverUrl,
});
admin.addDependency(server);
```

---

## Lambda 関数コード (`lambda/admin/index.py`)

```python
"""管理 UI Lambda: Recall.ai ボットを会議に参加させる独立サービス."""
import json
import os
import urllib.request
import urllib.error

RECALL_API_BASE = os.environ['RECALL_API_BASE']
FRONTEND_URL    = os.environ['FRONTEND_URL']
SERVER_URL      = os.environ['SERVER_URL']

_RECALL_TOKEN = None
_ADMIN_TOKEN  = None

def _ssm_get(name: str) -> str:
    import boto3
    ssm = boto3.client('ssm', region_name=os.environ.get('AWS_PARAM_REGION', 'ap-northeast-1'))
    return ssm.get_parameter(Name=name, WithDecryption=True)['Parameter']['Value']

def recall_token() -> str:
    global _RECALL_TOKEN
    if not _RECALL_TOKEN:
        _RECALL_TOKEN = _ssm_get('/ai-interviewer/RECALL_TOKEN')
    return _RECALL_TOKEN

def admin_token() -> str:
    global _ADMIN_TOKEN
    if not _ADMIN_TOKEN:
        _ADMIN_TOKEN = _ssm_get('/ai-interviewer/ADMIN_TOKEN')
    return _ADMIN_TOKEN

ADMIN_HTML = """<!DOCTYPE html>
<html lang="ja"><head><meta charset="UTF-8"><title>AI Interviewer Admin</title>
<style>
  body{font-family:sans-serif;max-width:560px;margin:40px auto;padding:0 20px}
  label{display:block;margin-top:16px;font-weight:bold}
  input,select{width:100%;padding:8px;margin-top:4px;box-sizing:border-box}
  button{margin-top:24px;padding:12px 24px;background:#2563eb;color:#fff;
         border:none;border-radius:4px;cursor:pointer;font-size:16px}
  #res{margin-top:20px;padding:12px;background:#f0f9ff;border-radius:4px;
       white-space:pre-wrap;font-family:monospace;display:none}
  #err{margin-top:20px;padding:12px;background:#fef2f2;border-radius:4px;display:none}
</style></head><body>
<h1>AI Interviewer — ボット参加</h1>
<form id="f">
  <label>ミーティング URL *
    <input type="url" name="meeting_url"
           placeholder="https://meet.google.com/xxx-xxx-xxx" required>
  </label>
  <label>シナリオ
    <select name="scenario">
      <option value="exit_interview">退職面談 (exit_interview)</option>
      <option value="compliance">コンプライアンス (compliance)</option>
      <option value="test">テスト (test)</option>
    </select>
  </label>
  <label><input type="checkbox" name="is_debug"> デバッグモード</label>
  <button type="submit">ボットを参加させる</button>
</form>
<div id="res"></div><div id="err"></div>
<script>
document.getElementById('f').addEventListener('submit', async e => {
  e.preventDefault();
  const d = new FormData(e.target);
  const res = document.getElementById('res'), err = document.getElementById('err');
  res.style.display = err.style.display = 'none';
  try {
    const r = await fetch('?token=' + new URLSearchParams(location.search).get('token'), {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({
        meeting_url: d.get('meeting_url'),
        scenario: d.get('scenario'),
        is_debug: d.get('is_debug') === 'on',
      }),
    });
    const j = await r.json();
    if (!r.ok) throw new Error(j.error || r.statusText);
    res.textContent = JSON.stringify(j, null, 2);
    res.style.display = 'block';
  } catch(e) {
    err.textContent = 'Error: ' + e.message;
    err.style.display = 'block';
  }
});
</script></body></html>"""

def handler(event, context):
    method = event.get('requestContext', {}).get('http', {}).get('method', 'GET')
    query  = event.get('queryStringParameters') or {}
    token  = query.get('token', '')

    if token != admin_token():
        return {'statusCode': 401, 'body': 'Unauthorized'}

    if method == 'GET':
        return {
            'statusCode': 200,
            'headers': {'Content-Type': 'text/html; charset=utf-8'},
            'body': ADMIN_HTML,
        }

    if method == 'POST':
        try:
            body = json.loads(event.get('body') or '{}')
        except json.JSONDecodeError:
            return _json_response(400, {'error': 'Invalid JSON'})

        meeting_url = body.get('meeting_url', '').strip()
        scenario    = body.get('scenario', 'exit_interview')
        is_debug    = bool(body.get('is_debug', False))

        if not meeting_url:
            return _json_response(400, {'error': 'meeting_url is required'})

        ws_url = SERVER_URL.replace('https://', 'wss://')
        camera_url = (
            f"{FRONTEND_URL}"
            f"?wss={ws_url}"
            f"&debug={str(is_debug).lower()}"
            f"&scenario={scenario}"
        )

        payload = {
            'meeting_url': meeting_url,
            'bot_name': 'Aya',
            'output_media': {'camera': {'kind': 'webpage', 'config': {'url': camera_url}}},
            'variant': {
                'zoom': 'web_4_core',
                'google_meet': 'web_4_core',
                'microsoft_teams': 'web_4_core',
            },
            'recording_config': {'include_bot_in_recording': {'audio': True}},
        }

        req = urllib.request.Request(
            f"{RECALL_API_BASE}/bot/",
            data=json.dumps(payload).encode(),
            headers={
                'Authorization': recall_token(),
                'Content-Type': 'application/json',
                'Accept': 'application/json',
            },
            method='POST',
        )
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                data = json.loads(resp.read())
        except urllib.error.HTTPError as e:
            detail = json.loads(e.read())
            return _json_response(502, {'error': 'Recall.ai error', 'detail': detail})

        bot_id = data.get('id')
        if bot_id:
            _notify_server(bot_id)

        return _json_response(200, {'status': 'ok', 'bot_id': bot_id})

    return _json_response(405, {'error': 'Method not allowed'})


def _notify_server(bot_id: str):
    try:
        req = urllib.request.Request(
            f"{SERVER_URL}/register-bot",
            data=json.dumps({'bot_id': bot_id}).encode(),
            headers={'Content-Type': 'application/json'},
            method='POST',
        )
        urllib.request.urlopen(req, timeout=5)
    except Exception:
        pass


def _json_response(status: int, body: dict) -> dict:
    return {
        'statusCode': status,
        'headers': {'Content-Type': 'application/json'},
        'body': json.dumps(body, ensure_ascii=False),
    }
```

---

## Python サーバー側の変更

### Dockerfile (`python-server/Dockerfile`)

```dockerfile
FROM python:3.11-slim

RUN apt-get update && apt-get install -y --no-install-recommends gcc \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt \
    && pip install --no-cache-dir boto3

COPY . .
RUN mkdir -p /app/chat_logs

EXPOSE 3000
CMD ["python", "startup.py"]
```

---

### startup.py (`python-server/startup.py`)

```python
"""コンテナ起動: SSM からシークレット取得 → server 起動."""
import os
from pathlib import Path

def pull_ssm_secrets():
    import boto3
    ssm = boto3.client('ssm', region_name=os.getenv('AWS_REGION', 'ap-northeast-1'))
    def get(name):
        return ssm.get_parameter(Name=name, WithDecryption=True)['Parameter']['Value']

    os.environ['OPENAI_API_KEY'] = get('/ai-interviewer/OPENAI_API_KEY')
    os.environ['RECALL_TOKEN']   = get('/ai-interviewer/RECALL_TOKEN')
    os.environ.setdefault('FRONTEND_URL', get('/ai-interviewer/FRONTEND_URL'))
    os.environ.setdefault('CORS_ALLOW_ORIGIN', os.environ['FRONTEND_URL'])
    Path('/app/ame-ai-agent.json').write_text(get('/ai-interviewer/GOOGLE_SA_JSON'))
    print("[startup] SSM secrets loaded", flush=True)

if not os.getenv('OPENAI_API_KEY'):
    pull_ssm_secrets()

import server
server.main()
```

---

### server.py の変更 (1行のみ)

CloudFront idle timeout 対策として heartbeat を追加:

```python
# 変更前 (server.py:77)
ws = web.WebSocketResponse(protocols=("realtime",))
# 変更後
ws = web.WebSocketResponse(protocols=("realtime",), heartbeat=30.0)
```

---

## SSM Parameter Store (事前設定)

```bash
# Python サーバー用シークレット
aws ssm put-parameter --name /ai-interviewer/OPENAI_API_KEY --type SecureString --value "sk-..."
aws ssm put-parameter --name /ai-interviewer/RECALL_TOKEN   --type SecureString --value "Token ..."
aws ssm put-parameter --name /ai-interviewer/GOOGLE_SA_JSON --type SecureString --value "$(cat python-server/ame-ai-agent.json)"

# 管理 UI (Lambda) 用シークレット
aws ssm put-parameter --name /ai-interviewer/ADMIN_TOKEN --type SecureString --value "$(openssl rand -hex 32)"

# フロントエンド URL (Cloudflare Pages URL)
aws ssm put-parameter --name /ai-interviewer/FRONTEND_URL --type String \
  --value "https://your-project.pages.dev"
```

---

## デプロイ手順

### 初回

```bash
# 1. SSM シークレットを事前設定 (上記コマンドを実行)

# 2. CDK セットアップ & bootstrap
cd infra && npm install && npx cdk bootstrap

# 3. 全スタックをデプロイ
#    (Dockerfile ビルド → ECR push → EC2 起動 → コンテナ起動 → CloudFront 作成 → Lambda 作成)
npx cdk deploy --all
# → ServerUrl: https://xxxx.cloudfront.net
# → AdminUrl:  https://xxxxx.lambda-url.ap-northeast-1.on.aws/
```

### アプリ更新時

```bash
cd infra && npx cdk deploy --all
# Dockerfile またはソースコードを変更していれば DockerImageAsset が差分を検知し
# 新しいイメージをビルド → ECR push → Custom Resource が SSM RunCommand を発行 → EC2 更新
```

---

## 管理 Web UI の使い方

CDK 出力の `AdminUrl` をブックマークして非エンジニアに共有:

```
https://xxxxx.lambda-url.ap-northeast-1.on.aws/?token=<ADMIN_TOKEN>
```

Python サーバーが停止・デプロイ中でも管理 UI は独立して動作する。

---

## 検証方法

1. `cdk deploy --all` のみでデプロイが完結することを確認
2. python-server のコードを変更して `cdk deploy --all` → EC2 上のコンテナが自動更新されることを確認
3. 管理 UI (`AdminUrl`) にアクセスして会議 URL を入力 → Recall.ai ボットが参加
4. `wss://xxxx.cloudfront.net` WebSocket 接続 + 音声リレー動作を確認
5. EC2 へ直接 HTTP アクセスできないことを確認 (CloudFront 経由のみ)
6. トークンなし / 不正トークンで 401 が返ることを確認
7. 短時間に 6 リクエスト以上送ると 429 が返ることを確認 (同時実行数制限)
