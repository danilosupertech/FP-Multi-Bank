.PHONY: install import dashboard dashboard-ollama agent-ollama-outros agent-ollama-all test lint clean reset-db

PYTHON=python
PIP=pip
OLLAMA_MODEL=qwen2.5:14b
AGENT_LIMIT=20
AGENT_CONFIDENCE=high

install:
	$(PIP) install -r requirements.txt

import:
	$(PYTHON) main.py

dashboard:
	$(PYTHON) -m streamlit run app/dashboard/streamlit_app.py

dashboard-ollama:
	powershell -Command "$$env:ENABLE_OLLAMA_CATEGORY='1'; $$env:ENABLE_WEB_RESEARCH='1'; $$env:WEB_SEARCH_PROVIDER='ollama'; $$env:OLLAMA_RESEARCH_MODEL='$(OLLAMA_MODEL)'; $(PYTHON) -m streamlit run app/dashboard/streamlit_app.py"

agent-ollama-outros:
	powershell -Command "$$env:ENABLE_OLLAMA_CATEGORY='1'; $$env:ENABLE_WEB_RESEARCH='1'; $$env:WEB_SEARCH_PROVIDER='ollama'; $$env:OLLAMA_RESEARCH_MODEL='$(OLLAMA_MODEL)'; $(PYTHON) scripts/run_dspy_reallocation_agent.py --limit $(AGENT_LIMIT) --confidence $(AGENT_CONFIDENCE) --scope outros"

agent-ollama-all:
	powershell -Command "$$env:ENABLE_OLLAMA_CATEGORY='1'; $$env:ENABLE_WEB_RESEARCH='1'; $$env:WEB_SEARCH_PROVIDER='ollama'; $$env:OLLAMA_RESEARCH_MODEL='$(OLLAMA_MODEL)'; $(PYTHON) scripts/run_dspy_reallocation_agent.py --limit $(AGENT_LIMIT) --confidence $(AGENT_CONFIDENCE) --scope all"

test:
	$(PYTHON) -m unittest discover -s tests

lint:
	$(PYTHON) -m compileall app main.py scripts tests

clean:
	powershell -Command "Get-ChildItem -Recurse -Directory -Filter __pycache__ | Remove-Item -Recurse -Force"

reset-db:
	powershell -Command "Remove-Item data/storage/financial.db -ErrorAction SilentlyContinue"
