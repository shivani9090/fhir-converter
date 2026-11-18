"""Browser E2E tests: drive the Swagger UI at /docs like a user would."""

import re

import pytest
from playwright.sync_api import Page, expect

pytestmark = pytest.mark.e2e


def test_docs_lists_all_endpoints(page: Page):
    page.goto("/docs")
    expect(page.get_by_role("heading", name=re.compile("FHIR Converter API"))).to_be_visible()
    for path in ["/health", "/templates/{source}", "/convert/ccda/{template}", "/convert/stu3"]:
        expect(page.locator(".opblock-summary-path", has_text=path).first).to_be_visible()


def test_try_it_out_health(page: Page):
    page.goto("/docs")
    page.locator(".opblock-summary-path", has_text="/health").first.click()
    page.get_by_role("button", name="Try it out").click()
    page.get_by_role("button", name="Execute").click()
    response = page.locator(".live-responses-table tbody td.response-col_status").first
    expect(response).to_have_text("200")
    expect(page.locator(".live-responses-table .microlight").first).to_contain_text("healthy")


def test_openapi_schema_served(page: Page, base_url):
    r = page.request.get(f"{base_url}/openapi.json")
    assert r.ok
    assert "/convert/ccda/{template}" in r.json()["paths"]


def test_openapi_declares_raw_request_bodies(page: Page, base_url):
    """_XML_BODY / _JSON_BODY openapi_extra give Swagger a raw body editor per endpoint."""
    paths = page.request.get(f"{base_url}/openapi.json").json()["paths"]
    ccda = paths["/convert/ccda/{template}"]["post"]["requestBody"]
    stu3 = paths["/convert/stu3"]["post"]["requestBody"]
    assert ccda["required"] and list(ccda["content"]) == ["application/xml"]
    assert stu3["required"] and list(stu3["content"]) == ["application/json"]


def _open_op(page: Page, method: str, path: str):
    block = page.locator(f".opblock-{method}").filter(
        has=page.locator(".opblock-summary-path", has_text=re.compile(rf"^{re.escape(path)}$"))
    ).first
    block.locator(".opblock-summary").click()
    block.get_by_role("button", name="Try it out").click()
    return block


def _status(block):
    return block.locator(".live-responses-table tbody td.response-col_status").first


def test_try_it_out_templates(page: Page):
    page.goto("/docs")
    block = _open_op(page, "get", "/templates/{source}")
    block.locator("select").first.select_option("stu3")
    block.get_by_role("button", name="Execute").click()
    expect(_status(block)).to_have_text("200")
    expect(block.locator(".live-responses-table .microlight").first).to_contain_text('"Patient"')


def test_try_it_out_convert_ccda(page: Page, samples):
    page.goto("/docs")
    block = _open_op(page, "post", "/convert/ccda/{template}")
    block.locator('input[placeholder="template"]').fill("CCD")
    block.locator("textarea.body-param__text").fill((samples / "ccda" / "CCD.ccda").read_text(encoding="utf-8"))
    block.get_by_role("button", name="Execute").click()
    expect(_status(block)).to_have_text("200", timeout=60_000)
    expect(block.locator(".live-responses-table .microlight").first).to_contain_text('"Bundle"')


def test_try_it_out_convert_stu3(page: Page, samples):
    page.goto("/docs")
    block = _open_op(page, "post", "/convert/stu3")
    block.locator("textarea.body-param__text").fill((samples / "stu3" / "Patient.json").read_text(encoding="utf-8"))
    block.get_by_role("button", name="Execute").click()
    expect(_status(block)).to_have_text("200", timeout=60_000)
    expect(block.locator(".live-responses-table .microlight").first).to_contain_text('"Patient"')


def test_try_it_out_convert_stu3_missing_resource_type(page: Page):
    page.goto("/docs")
    block = _open_op(page, "post", "/convert/stu3")
    block.locator("textarea.body-param__text").fill('{"id": "x"}')
    block.get_by_role("button", name="Execute").click()
    # 400 is not declared in the OpenAPI spec, so Swagger renders "400 Undocumented"
    expect(_status(block)).to_have_text(re.compile(r"^\s*400\b"))


def test_try_it_out_convert_ccda_unknown_template(page: Page, samples):
    page.goto("/docs")
    block = _open_op(page, "post", "/convert/ccda/{template}")
    block.locator('input[placeholder="template"]').fill("NoSuchTemplate")
    block.locator("textarea.body-param__text").fill((samples / "ccda" / "CCD.ccda").read_text(encoding="utf-8"))
    block.get_by_role("button", name="Execute").click()
    expect(_status(block)).to_have_text(re.compile(r"^\s*404\b"))
    expect(block.locator(".live-responses-table .microlight").first).to_contain_text("NoSuchTemplate")


def test_try_it_out_convert_stu3_explicit_template(page: Page, samples):
    page.goto("/docs")
    block = _open_op(page, "post", "/convert/stu3")
    block.locator('input[placeholder="template"]').fill("Observation")
    block.locator("textarea.body-param__text").fill((samples / "stu3" / "Observation.json").read_text(encoding="utf-8"))
    block.get_by_role("button", name="Execute").click()
    expect(_status(block)).to_have_text("200", timeout=60_000)
    expect(block.locator(".live-responses-table .microlight").first).to_contain_text('"Observation"')


def test_try_it_out_convert_ccda_malformed_xml(page: Page):
    page.goto("/docs")
    block = _open_op(page, "post", "/convert/ccda/{template}")
    block.locator('input[placeholder="template"]').fill("CCD")
    block.locator("textarea.body-param__text").fill("<ClinicalDocument><unclosed>")
    block.get_by_role("button", name="Execute").click()
    expect(_status(block)).to_have_text(re.compile(r"^\s*422\b"), timeout=60_000)
    expect(block.locator(".live-responses-table .microlight").first).to_contain_text("Conversion failed")
