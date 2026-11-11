.PHONY: help test run clean

.DEFAULT: help

help:
	@echo "make test   - unit tests (no network, no Docker)"
	@echo "make run    - build the fixtures of the app in dev.env in Docker, into data/output/"
	@echo "make clean  - remove data/output/"

test:
	python3 -m pytest -q

run: clean
	mkdir -p data/input data/output
	docker compose down --remove-orphans
	docker compose build
	docker compose up --exit-code-from processor
	@find data/output -type f | sort

clean:
	rm -rf data/output
