.PHONY: run-demo

run-demo:
	docker compose build demo
	docker compose run --rm demo
