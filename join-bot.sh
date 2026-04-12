# 環境変数が既に設定されている場合はそれを使用し、未設定の場合はデフォルト値を使用
export FRONTEND_URL="${FRONTEND_URL:-https://voice-agent-demo-1yr.pages.dev}"
export RECALL_TOKEN="${RECALL_TOKEN:-xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx}"
export NGROK_URL="${NGROK_URL:-52.193.78.104:3000}"
export MEETING_URL="${MEETING_URL:-https://meet.google.com/qgm-dpzi-cwn}"
export IS_DEBUG="${IS_DEBUG:-false}"
export SCENARIO="${SCENARIO:-exit_interview}" # exit_interview または compliance を指定可能

if [ "$NGROK_URL" = "*:3000" ]; then
  ws_url="ws://${NGROK_URL}"
  http_url="http://${NGROK_URL}"
else
  ws_url="wss://${NGROK_URL}"
  http_url="https://${NGROK_URL}"
fi


# NGROK を使う場合は config.url は ws じゃなくて wss にする
# TODO: 自前サーバーの場合も wss を使えるようにしたい
RESPONSE=$(curl --silent --request POST \
  --url https://ap-northeast-1.recall.ai/api/v1/bot/ \
  --header "Authorization: ${RECALL_TOKEN}" \
  --header 'accept: application/json' \
  --header 'content-type: application/json' \
  --data '{
    "meeting_url": "'"${MEETING_URL}"'",
    "bot_name": "Aya",
    "output_media": {
      "camera": {
        "kind": "webpage",
        "config": {
          "url": "'"${FRONTEND_URL}"'?wss='"${ws_url}"'&debug='"${IS_DEBUG}"'&scenario='"${SCENARIO}"'"
        }
      }
    },
    "variant": {
      "zoom": "web_4_core",
      "google_meet": "web_4_core",
      "microsoft_teams": "web_4_core"
    },
    "recording_config": {
      "include_bot_in_recording": {
        "audio": true
      }
    }
  }')

echo "$RESPONSE"

# bot_id を抽出してサーバーに登録する (面談完了時の自動退出用)
BOT_ID=$(echo "$RESPONSE" | python3 -c "import sys,json; print(json.load(sys.stdin).get('id',''))" 2>/dev/null)
if [ -n "$BOT_ID" ]; then
  echo "Registering bot_id=${BOT_ID} with server..." >&2
  curl --silent --request POST \
    --url "${http_url}/register-bot" \
    --header 'content-type: application/json' \
    --data '{"bot_id": "'"${BOT_ID}"'"}' >&2
  echo "" >&2
else
  echo "Warning: Could not extract bot_id from response" >&2
fi

