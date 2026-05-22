# 狮选 LionPick — common dev tasks
# Usage: make <target>

.PHONY: help dev backend ingest eval ios ios-sim ios-device resign mock test sync-a100 fmt clean install-cli check-secrets

help:
	@echo "狮选 LionPick — make targets"
	@echo ""
	@echo "  make backend     run the FastAPI dev server (http://localhost:8000)"
	@echo "  make mock        run the offline mock backend (no Doubao, no Qdrant)"
	@echo "  make ingest      one-time RAG ingest of data/seed/"
	@echo "  make eval        run the golden eval set; report recall@5"
	@echo "  make ios         regenerate AAALionApp.xcodeproj (needs xcodegen)"
	@echo "  make ios-sim     build + run on iPhone 17 Pro simulator"
	@echo "  make ios-device  build + install on a paired iPhone (auto-detects UUID)"
	@echo "  make resign      alias for ios-device; reminder that free certs expire weekly"
	@echo "  make sync-a100   rsync project to uc:~/shufeng/AAALion-/"
	@echo "  make fmt         format Python with ruff (and swiftformat if installed)"
	@echo "  make clean       remove .venv, __pycache__, DerivedData"
	@echo "  make install-cli symlink ./tools/aaalion into /usr/local/bin so you can run \`aaalion <target>\` from anywhere"
	@echo "  make check-secrets scan staged files for API-key-shaped strings before commit"

backend:
	cd server && python -m uvicorn app.main:app --reload --host 0.0.0.0 --port 8000

mock:
	python tools/mock_backend.py

ingest:
	cd rag && python -m ingest.run

eval:
	cd rag && python -m eval.run

ios:
	cd client/AAALionApp && xcodegen

ios-sim:
	@cd client/AAALionApp && xcodegen
	@xcodebuild -project client/AAALionApp/AAALionApp.xcodeproj -scheme AAALionApp \
	  -destination 'platform=iOS Simulator,name=iPhone 17 Pro' \
	  -derivedDataPath /tmp/lionpick-derived build
	@xcrun simctl boot "iPhone 17 Pro" 2>/dev/null; true
	@xcrun simctl install booted /tmp/lionpick-derived/Build/Products/Debug-iphonesimulator/狮选.app
	@xcrun simctl launch booted com.aaalion.lionpick
	@open -a Simulator

ios-device:
	@cd client/AAALionApp && xcodegen
	@DEVICE_UUID=$$(xcrun devicectl list devices 2>/dev/null | awk '/iPhone/ && /connected/ {print $$3; exit}') ; \
	if [ -z "$$DEVICE_UUID" ]; then \
	  echo "No iPhone connected. Plug it in via USB and trust the Mac, then retry." ; \
	  exit 1 ; \
	fi ; \
	echo "device: $$DEVICE_UUID" ; \
	xcodebuild -project client/AAALionApp/AAALionApp.xcodeproj -scheme AAALionApp \
	  -destination "platform=iOS,id=$$DEVICE_UUID" \
	  -derivedDataPath /tmp/lionpick-derived-device \
	  -allowProvisioningUpdates build && \
	xcrun devicectl device install app --device "$$DEVICE_UUID" \
	  /tmp/lionpick-derived-device/Build/Products/Debug-iphoneos/狮选.app

# Re-sign + re-install the app on a connected iPhone. Free Apple ID
# Personal Team certs/profiles expire ~7 days after issue, so run this
# weekly (or before any demo) to refresh the provisioning profile.
resign: ios-device
	@echo ""
	@echo "App re-signed and re-installed. Cert valid for ~7 more days."
	@echo "Set a calendar reminder for next $$(date -v+6d '+%Y-%m-%d') if you'll demo after that."

sync-a100:
	rsync -az --exclude=.venv --exclude=.git --exclude=screenshots \
		--exclude=data/extra --exclude='__pycache__' --exclude='*.xcodeproj' \
		./ uc:~/shufeng/AAALion-/

fmt:
	@which ruff > /dev/null && ruff format server rag tools || echo "ruff not installed; skipping python format"
	@which swiftformat > /dev/null && swiftformat client/AAALionApp/AAALionApp || echo "swiftformat not installed; skipping swift format"

clean:
	find . -type d -name __pycache__ -prune -exec rm -rf {} +
	rm -rf .venv server/.venv rag/.venv
	rm -rf client/AAALionApp/AAALionApp.xcodeproj

install-cli:
	@dest=/usr/local/bin/aaalion ; \
	src="$(CURDIR)/tools/aaalion" ; \
	if [ -w /usr/local/bin ]; then \
	  ln -sf "$$src" "$$dest" ; \
	  echo "Linked $$src → $$dest. Now run e.g. \`aaalion ios\` from anywhere." ; \
	else \
	  echo "Need sudo: sudo ln -sf $$src $$dest" ; \
	fi

check-secrets:
	@tools/check-secrets.sh
