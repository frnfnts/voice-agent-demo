up:
	cd node-server && npm run dev

# conversation_config.ts を編集したらビルドする
build:
	cd client && npm run build

ngrok:
	ngrok 3000

join:
	./join-bot.sh

.PHONY: client
