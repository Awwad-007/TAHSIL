.PHONY: run-demo test

test:
	python -m pytest tests/ -v

run-demo:
	docker compose build demo
	docker compose run --rm demo
