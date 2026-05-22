# 狮选 LionPick — common dev tasks
# Usage: make <target>

.PHONY: help dev backend ingest eval ios mock test sync-a100 fmt clean install-cli check-secrets

help:
	@echo "狮选 LionPick — make targets"
	@echo ""
	@echo "  make backend     run the FastAPI dev server (http://localhost:8000)"
	@echo "  make mock        run the offline mock backend (no Doubao, no Qdrant)"
	@echo "  make ingest      one-time RAG ingest of data/seed/"
	@echo "  make eval        run the golden eval set; report recall@5"
	@echo "  make ios         regenerate AAALionApp.xcodeproj (needs xcodegen)"
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
