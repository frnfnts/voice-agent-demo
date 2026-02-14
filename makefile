up:
	python3 python-server/server.py

# conversation_config.ts を編集したらビルドする
build:
	cd client && npm run build

ngrok:
	ngrok http 3000

front-test:
	cd client && npm run dev
# 	http://localhost:5173/?wss=wss://eloy-astrographic-sydney.ngrok-free.dev&debug=true

join:
	./join-bot.sh

upload-chat-logs:
	cd python-server && \
	python3 upload_logs.py
# 	python3 upload_logs.py --dry-run

.PHONY: client
