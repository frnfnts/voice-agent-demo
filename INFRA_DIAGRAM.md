# インフラ構成図

## 全体構成

```mermaid
flowchart TB
    %% ────────────────────────────
    %% 利用者
    %% ────────────────────────────
    DEV(["👩‍💻 エンジニア\n(ローカル)"])
    NONENG(["👤 社内管理者"])
    INTERVIEWEE(["🧑 面接対象者"])

    %% ────────────────────────────
    %% Cloudflare (CDK 管理外)
    %% ────────────────────────────
    subgraph CF_PAGES["Cloudflare Pages（CDK 管理外）"]
        FRONTEND["フロントエンド\nhttps://xxx.pages.dev\ndevelop push → 自動デプロイ"]
    end

    %% ────────────────────────────
    %% AWS
    %% ────────────────────────────
    subgraph AWS["☁️ AWS  ap-northeast-1"]

        subgraph STORE["ストレージ / シークレット"]
            ECR[("CDK マネージド ECR\n(DockerImageAsset が自動管理)")]
            SSM[("SSM Parameter Store\nSecureString\nOPENAI_API_KEY\nRECALL_TOKEN\nADMIN_TOKEN\nGOOGLE_SA_JSON")]
        end

        subgraph BACK["AiInterviewerServer Stack"]
            DOCKERASSET["DockerImageAsset\n(cdk deploy 時に\nDockerfile をビルド)"]
            CUSTOM["Custom Resource (Lambda)\n(イメージ URI 変化時に\nSSM RunCommand を発行)"]
            CF["CloudFront\nhttps://xxxx.cloudfront.net\nwss://xxxx.cloudfront.net\nHTTPS / WSS 終端"]
            EC2["EC2  t4g.micro  ARM64\nDocker: python-server\naiohttp + LangGraph\n:3000\n※ CloudFront IP のみ許可"]
            EIP["Elastic IP"]
        end

        subgraph ADMINBOX["AiInterviewerAdmin Stack（Python サーバーと完全独立）"]
            LAMBDA["λ Lambda\nai-interviewer-admin\n同時実行数: 5\nトークン認証"]
            FNURL["Function URL\nhttps://xxxxx.lambda-url\n.ap-northeast-1.on.aws"]
        end

    end

    %% ────────────────────────────
    %% 外部サービス
    %% ────────────────────────────
    subgraph EXT["外部サービス"]
        OPENAI["OpenAI\nRealtime API\nwss://api.openai.com"]
        RECALL["Recall.ai\nBot API"]
        GDRIVE["Google Drive\n(プロンプト)"]
    end

    %% ════════════════════════════
    %% デプロイフロー (cdk deploy のみ)
    %% ════════════════════════════
    DEV -- "cdk deploy --all" --> DOCKERASSET
    DOCKERASSET -- "docker build & push" --> ECR
    ECR -- "imageUri" --> CUSTOM
    CUSTOM -- "SSM RunCommand\n(deploy.sh imageUri)" --> EC2
    EC2 -- "docker pull" --> ECR
    SSM -- "シークレット注入\n(コンテナ起動時)" --> EC2
    SSM -- "シークレット取得\n(コールドスタート時)" --> LAMBDA

    %% ════════════════════════════
    %% 管理フロー (社内管理者)
    %% ════════════════════════════
    NONENG -- "会議URL / シナリオ入力\n?token=xxx" --> FNURL
    FNURL --> LAMBDA
    LAMBDA -- "POST /bot/\nボット作成" --> RECALL
    LAMBDA -- "POST /register-bot\n(ベストエフォート)" --> CF

    %% ════════════════════════════
    %% 実行時フロー (面接)
    %% ════════════════════════════
    RECALL -- "会議参加\n(カメラ: フロントエンドを表示)" --> FRONTEND
    FRONTEND -- "WSS 接続" --> CF
    CF -- "HTTP :3000\n(CloudFront IP のみ)" --> EC2
    EC2 -- "audio stream\n(Realtime API)" --> OPENAI
    EC2 -- "プロンプト取得" --> GDRIVE
    EC2 --- EIP
    INTERVIEWEE -- "会議参加" --> RECALL

    %% ════════════════════════════
    %% スタイル
    %% ════════════════════════════
    classDef awsOrange fill:#FF9900,color:#000,stroke:#c67100
    classDef external  fill:#e8f4fd,color:#000,stroke:#4a90d9
    classDef user      fill:#f0fdf4,color:#000,stroke:#16a34a
    classDef cfpages   fill:#f6821f,color:#fff,stroke:#c46210

    class ECR,SSM,CF,EC2,EIP,LAMBDA,FNURL,DOCKERASSET,CUSTOM awsOrange
    class OPENAI,RECALL,GDRIVE external
    class DEV,NONENG,INTERVIEWEE user
    class FRONTEND cfpages
```

---

## デプロイフロー (`cdk deploy --all`)

```mermaid
sequenceDiagram
    actor Eng as エンジニア (ローカル)
    participant CDK as CDK CLI
    participant ECR as ECR (CDK マネージド)
    participant CFn as CloudFormation
    participant CR as Custom Resource (Lambda)
    participant EC2 as EC2

    Eng->>CDK: cdk deploy --all

    CDK->>CDK: DockerImageAsset:\ndocker build (ARM64)
    CDK->>ECR: docker push :xxxx (digest ベース)
    CDK->>CFn: テンプレート + imageUri を送信

    CFn->>CR: imageUri が前回と異なる場合のみ\nCustomResource を更新
    CR->>EC2: SSM RunCommand:\ndeploy.sh <imageUri>
    EC2->>ECR: docker pull <imageUri>
    EC2->>EC2: docker stop & rm & run
    EC2-->>CR: command complete
    CR-->>CFn: SUCCESS
    CFn-->>CDK: deploy complete
    CDK-->>Eng: Outputs: ServerUrl / AdminUrl
```

---

## 音声面談フロー

```mermaid
sequenceDiagram
    actor Admin as 社内管理者
    participant Lambda as Lambda (管理 UI)
    participant Recall as Recall.ai API
    participant Bot as Recall.ai Bot
    participant CF as CloudFront
    participant Server as EC2 (python-server)
    participant OpenAI as OpenAI Realtime API

    Admin->>Lambda: POST /?token=xxx\n{ meeting_url, scenario }
    Lambda->>Recall: POST /api/v1/bot/\n{ camera.url: frontend?wss=... }
    Recall-->>Lambda: { id: bot_id }
    Lambda->>Server: POST /register-bot { bot_id }

    Recall->>Bot: ボット起動・会議参加
    Bot->>CF: WebSocket 接続 (wss://)
    CF->>Server: HTTP :3000 (WebSocket Upgrade)
    Server->>OpenAI: WebSocket 接続 (wss://)

    loop 面談中
        Bot-->>Server: 音声ストリーム (PCM16)
        Server-->>OpenAI: input_audio_buffer.append
        OpenAI-->>Server: response.audio.delta
        Server-->>Bot: 音声再生
    end

    Server->>Bot: 面談完了 → 接続切断
```

---

## ネットワーク境界

```mermaid
flowchart LR
    subgraph pub["インターネット"]
        bot["Recall.ai Bot\n/ ブラウザ"]
        atk["不正アクセス ✗"]
    end

    subgraph edge["CloudFront Edge"]
        tls["HTTPS / WSS\nTLS 終端"]
    end

    subgraph vpc["AWS VPC（Default）"]
        subgraph sg["Security Group"]
            ec2["EC2 :3000\n← CloudFront IP のみ\n(pl-58a04531)"]
        end
    end

    bot -->|"wss:// / https://"| tls
    tls -->|"HTTP :3000\nCloudFront IP のみ許可"| ec2
    atk -. "直接アクセス 遮断" .-> ec2
```

---

## サービス一覧

| サービス | スタック | 役割 | URL |
|---|---|---|---|
| **CloudFront** | AiInterviewerServer | Python サーバーへの HTTPS / WSS エンドポイント | `https://xxxx.cloudfront.net` |
| **EC2 t4g.micro** | AiInterviewerServer | Python (aiohttp + LangGraph) を Docker で実行 | — |
| **Elastic IP** | AiInterviewerServer | EC2 の固定 IP | — |
| **DockerImageAsset** | AiInterviewerServer | cdk deploy 時に Dockerfile をビルドし ECR へプッシュ | — |
| **Custom Resource** | AiInterviewerServer | imageUri 変化時に SSM RunCommand で EC2 を更新 | — |
| **Lambda (管理 UI)** | AiInterviewerAdmin | 管理 UI と Recall.ai ボット参加 API | `https://xxxxx.lambda-url.ap-northeast-1.on.aws` |
| **SSM Parameter Store** | 共通 | API キー・トークン等のシークレット管理 | — |
| **Cloudflare Pages** | CDK 管理外 | フロントエンド (develop push で自動デプロイ) | `https://xxx.pages.dev` |

## デプロイトリガー一覧

| 対象 | トリガー | 方法 |
|---|---|---|
| バックエンド + インフラ | エンジニアが手動実行 | `cdk deploy --all` |
| 管理 UI のみ更新 | エンジニアが手動実行 | `cdk deploy AiInterviewerAdmin` |
| フロントエンド | develop への push (自動) | Cloudflare Pages GitHub 連携 |
