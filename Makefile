SHELL := /bin/bash
VENV   := .venv
PY     := $(VENV)/bin/python
ifeq ($(OS),Windows_NT)
	PY := $(VENV)/Scripts/python.exe
endif

.DEFAULT_GOAL := help

.PHONY: help
help:  ## Lista os comandos disponíveis
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) \
		| awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-16s\033[0m %s\n", $$1, $$2}'

.PHONY: install
install:  ## Cria o ambiente virtual e instala as dependências
	python -m venv $(VENV)
	$(PY) -m pip install --upgrade pip
	$(PY) -m pip install -r requirements-dev.txt

.PHONY: test
test:  ## Roda toda a suíte (um processo por serviço — ver tests/conftest.py)
	$(PY) -m pytest tests/lancamentos
	$(PY) -m pytest tests/consolidado
	$(PY) -m pytest tests/authorizer

.PHONY: cov
cov:  ## Roda a suíte com relatório de cobertura
	$(PY) -m pytest tests/lancamentos --cov=apps/lancamentos --cov-report=term-missing
	$(PY) -m pytest tests/consolidado --cov=apps/consolidado --cov-report=term-missing
	$(PY) -m pytest tests/authorizer --cov=apps/authorizer --cov-report=term-missing

.PHONY: lacunas
lacunas:  ## Mostra as lacunas conhecidas do MVP, cobertas por teste
	@$(PY) -m pytest tests/lancamentos tests/consolidado --collect-only -q 2>/dev/null || true
	$(PY) -m pytest tests/lancamentos/test_lacunas_conhecidas.py -rx
	$(PY) -m pytest tests/consolidado/test_lacunas_conhecidas.py -rx

.PHONY: up
up:  ## Sobe a infra local emulada (sem Docker) e cria tabelas e filas
	@echo "Subindo emulador AWS na porta 5000..."
	@$(PY) -m moto.server -p 5000 > .moto.log 2>&1 & \
	 for i in $$(seq 1 20); do \
	   $(PY) -c "import urllib.request;urllib.request.urlopen('http://localhost:5000')" 2>/dev/null && break; \
	   sleep 1; \
	 done
	$(PY) scripts/preparar_ambiente_local.py

.PHONY: run
run:  ## Sobe a API local em http://localhost:3000 (requer `make up` antes)
	$(PY) scripts/api_local.py

.PHONY: up-docker
up-docker:  ## Alternativa: sobe DynamoDB Local + ElasticMQ via docker compose
	docker compose up -d --wait
	ENDPOINT_DYNAMODB=http://localhost:8000 ENDPOINT_SQS=http://localhost:9324 \
	  $(PY) scripts/preparar_ambiente_local.py

.PHONY: down
down:  ## Derruba a infra local
	-docker compose down -v 2>/dev/null
	-pkill -f "moto.server" 2>/dev/null
	@rm -f .moto.log

.PHONY: carga
carga:  ## Executa o teste de carga de 50 rps (requer k6 e API_URL)
	@test -n "$(API_URL)" || (echo "defina API_URL, ex.: make carga API_URL=https://..." && exit 1)
	k6 run -e API_URL=$(API_URL) tests/carga/consolidado-50rps.js

.PHONY: package
package:  ## Empacota as funções Lambda a partir do código-fonte
	$(PY) scripts/empacotar_lambdas.py

.PHONY: clean
clean:  ## Remove artefatos gerados
	rm -rf .pytest_cache .coverage htmlcov dist
	find . -type d -name __pycache__ -prune -exec rm -rf {} +
