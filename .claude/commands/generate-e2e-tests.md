1. Start the API in the background: `python run_api.py --port 8000`.
2. Use the Playwright MCP tools to open http://127.0.0.1:8000/docs, inspect every endpoint,
   and call each one with files from data/sample/ccda and data/sample/stu3.
   Also try error cases: empty body, non-UTF-8 body, unknown template, invalid JSON,
   STU3 payload without resourceType.
3. Read api/app.py, fhir_converter/renderers.py and fhir_converter/__main__.py and list the
   important classes and functions. Every one must be reached by at least one E2E path
   (HTTP API, Swagger UI, or the `python -m fhir_converter` CLI).
4. Write or update pytest-playwright tests in tests/e2e/. Reuse the fixtures in
   tests/e2e/conftest.py (it starts uvicorn on a free port). Mark every test with
   `pytestmark = pytest.mark.e2e`.
5. Run `pytest tests/e2e -m e2e` and fix failures. Do not weaken assertions to make tests pass;
   if the app is wrong, report it.
6. Use only the synthetic sample files already in the repo. Never add real patient data.
7. Stop the API server, then finish with a coverage table: function/class -> test name.
