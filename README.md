# 使い方
## 音声エージェント

- `join-bot.sh` の `ngrok_url` と `meeting_url` を自分のものに変更してください。
- AI の挙動を変更したい場合は、google docs のプロンプトを編集してください。
  - [退職面談用プロンプト](https://docs.google.com/document/d/1cQSHjpoijqEkbvU8h5ZlMzk3qIdy6u4gjL4qXM4BA9w/edit?usp=sharing)
  - [コンプライアンスシナリオ用プロンプト](https://docs.google.com/document/d/17X_7fQzE14K6FFYWj9PQTPFLCHzsfVG8-phoPH-f37g/edit?usp=sharing)
- 環境変数を設定して、`join-bot.sh` を実行してください。

```bash
export FRONTEND_URL="https://your-frontend-url.com"
export RECALL_TOKEN="your_recall_token"
export NGROK_URL="your_ngrok_url:port"
export SCENARIO="exit_interview" # exit_interview または compliance を指定可能
export MEETING_URL="your_meeting_url"
export IS_DEBUG="true" # デバッグモードを有効にする場合は true
bash join-bot.sh
```

# 注意点等

- bot が会議に参加してから、バックエンドサーバーと接続されるまで少し時間がかかります。Websocket の接続が完了するまでお待ち下さい。
- 本番環境では、`ws://` の代わりに `wss://` を使用することが推奨されます。
- フロントエンドは、変更が `main` ブランチにプッシュされると、自動的に個人の Cloudflare Pages にデプロイされます。

## テキストチャット版

`text_chat.py` — テキストベースの面談テスト CLI です。音声パイプラインを使わずにプロンプトを高速検証できます。

### 使い方

```bash
cd python-server
python3 text_chat.py --mode auto --persona witness_direct --scenario compliance
```

- `--persona` で面談者のペルソナ、`--scenario` でシナリオを指定します。
- プロンプトは `python-server/` ディレクトリにあるテキストファイル（`prompt.txt`, `prompt_compliance.txt` など）を使用します。ローカルで編集してテストを繰り返し、最終的に Google Docs にペーストする使い方を想定しています。

### 会話ログ

- 会話ログは `chat_logs/` に JSON 形式で自動保存されます。
- `upload_logs.py` を使って [Google Drive](https://drive.google.com/drive/u/1/folders/1rmeUlt3rjX9RzK9S9FdG1mTx1B-8dpAE) にアップロードして共有できます。

```bash
cd python-server
python3 upload_logs.py
```

----- ↓ ここから original README.md -----
# Recall.ai Real-Time Voice Agent

### [Watch the Demo Here!](https://www.loom.com/share/2a02fac2643441c1990da861e829892c)

This demo application uses Recall.ai's [Output Media](https://docs.recall.ai/docs/stream-media) feature and OpenAI's [real-time API](https://platform.openai.com/docs/guides/realtime) to add an interactive voice agent to meetings.

## Prerequisites

1. [Node.js](https://nodejs.org/en/) (for Node.js server implementation)
2. [Python 3.8+](https://www.python.org/downloads/) (for Python server implementation)
3. [Ngrok](https://ngrok.com/docs/getting-started/)
4. [Recall.ai API Key](https://www.recall.ai/)
5. [OpenAI API Key](https://platform.openai.com/docs/overview)

## Installation

### Clone the Repository

```bash
git clone ...
```

### Install Dependencies

#### Client

```bash
cd client
npm install
```

#### Server

The server implementation is available in both Node.js and Python. Choose your preferred implementation:

##### Node.js Implementation

```bash
cd ../node-server
npm install
```

##### Python Implementation

```bash
cd ../python-server
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

## Configuration

### OpenAI API Key
Note: You **must** add credits to your OpenAI account before running this demo. If your account has no credits, the demo will connect successfully, but the bot will not respond to anything you say in the meeting.

#### Node.js Server

In the node-server directory, copy the `.env.example` file and rename it to `.env`. Then, add your OpenAI API key.

#### Python Server

In the python-server directory, copy the `.env.example` file and rename it to `.env`. Then, add your OpenAI API key. The PORT is optional and defaults to 3000 if not specified.

## Quickstart

If you want to quickly test the functionality of this application, you don't need to host the frontend yourself. You can use our pre-hosted demo frontend at [https://recallai-demo.netlify.app](https://recallai-demo.netlify.app). However, you will still need to provide your OpenAI API key and ngrok URL.

1. Start your backend server (choose either Node.js or Python implementation) and expose it using ngrok:

Node.js:

```bash
cd node-server
npm run dev
```

Python:

```bash
cd python-server
python server.py
```

Then in a separate terminal:

```bash
ngrok http 3000
```

2. Create a bot by sending the following curl request, replacing YOUR_RECALL_TOKEN and YOUR_NGROK_URL with your values:

```bash
curl --request POST \
  --url https://us-east-1.recall.ai/api/v1/bot/ \
  --header 'Authorization: YOUR_RECALL_TOKEN' \
  --header 'accept: application/json' \
  --header 'content-type: application/json' \
  --data '{
    "meeting_url": "YOUR_MEETING_URL",
    "bot_name": "Recall.ai Notetaker",
    "output_media": {
      "camera": {
        "kind": "webpage",
        "config": {
          "url": "https://recallai-demo.netlify.app?wss=wss://YOUR_NGROK_URL"
        }
      }
    },
    "variant": {
      "zoom": "web_4_core",
      "google_meet": "web_4_core",
      "microsoft_teams": "web_4_core"
    }
  }'
```

The bot will join your meeting URL and stream the demo webpage's content directly to your meeting.

If you'd like to customize the webpage shown by the bot, or change the interaction with the OpenAI agent, follow the complete setup instructions below.

## Customizing the Webpage

### Local Development Setup

Navigate to the client directory and start the development server:

```bash
cd client
npm run dev
```

The client will be available at `http://localhost:5173`.

### Modifying the Agent

You can modify the initial prompt of the agent by editing the `conversation_config.ts` file.

### Building for Production

Build the client application:

```bash
npm run build
```

The built files will be in the `dist` directory, ready to be deployed to your hosting service.

Once the frontend is deployed on a hosting service, update your bot configuration to use your custom webpage URL:

```json
{
  "output_media": {
    "kind": "webpage",
    "config": {
      "url": "https://your-custom-url.com?wss=wss://your-server.com"
    }
  }
}
```

Using this, you will be able to interact with a customized voice agent.

## Acknowledgements

This project incorporates code from [OpenAI's real-time API demo](https://github.com/openai/openai-realtime-console), which is under the MIT License.

## FAQ

### The webpage shows that my bot is connected, why isn't it replying to me in the meeting?
If the webpage is showing a successful connection but the bot isn't speaking, it's likely that you need to add credits to your OpenAI account. If your account has no credits, the demo will connect successfully, but the bot will not respond to anything you say in the meeting.
