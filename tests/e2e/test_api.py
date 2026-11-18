"""E2E tests for the HTTP API (api/app.py).

Covers: health, templates/list_templates, _require_template, convert_ccda
(CcdaRenderer), convert_stu3 (Stu3FhirRenderer + _stu3_template_from), and the
error mapping in _render.
"""

import json
from pathlib import Path

import pytest

pytestmark = pytest.mark.e2e

XML = {"Content-Type": "application/xml"}
JSON = {"Content-Type": "application/json"}

# (sample file, root template) pairs that exist in data/sample/ccda and fhir_converter/templates/ccda
CCDA_CASES = [
    ("CCD.ccda", "CCD"),
    ("C-CDA_R2-1_CCD.ccda", "CCD"),
    ("Consultation_Note.ccda", "ConsultationNote"),
    ("Discharge_Summary.ccda", "DischargeSummary"),
    ("History_and_Physical.ccda", "HistoryandPhysical"),
    ("Operative_Note.ccda", "OperativeNote"),
    ("Procedure_Note.ccda", "ProcedureNote"),
    ("Progress_Note.ccda", "ProgressNote"),
    ("Referral_Note.ccda", "ReferralNote"),
    ("Transfer_Summary.ccda", "TransferSummary"),
]

STU3_RESOURCES = [
    "AllergyIntolerance",
    "Condition",
    "Encounter",
    "Immunization",
    "Medication",
    "Observation",
    "Organization",
    "Patient",
    "Person",
    "Procedure",
]


# --------------------------------------------------------------------------- #
# Meta endpoints
# --------------------------------------------------------------------------- #
def test_health(api):
    r = api.get("/health")
    assert r.ok
    assert "healthy" in r.json()["status"]


@pytest.mark.parametrize("source, expected", [("ccda", "CCD"), ("stu3", "Patient")])
def test_list_templates(api, source, expected):
    r = api.get(f"/templates/{source}")
    assert r.ok
    body = r.json()
    assert body["source"] == source
    assert expected in body["templates"]
    assert body["templates"] == sorted(body["templates"])


def test_list_templates_rejects_unknown_source(api):
    assert api.get("/templates/hl7v2").status == 422


# --------------------------------------------------------------------------- #
# CCDA -> R4
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("sample, template", CCDA_CASES)
def test_ccda_converts_to_bundle(api, samples, sample, template):
    r = api.post(f"/convert/ccda/{template}", data=(samples / "ccda" / sample).read_bytes(), headers=XML)
    assert r.ok, r.text()
    assert r.headers["content-type"].startswith("application/fhir+json")
    bundle = r.json()
    assert bundle["resourceType"] == "Bundle"
    types = {e["resource"]["resourceType"] for e in bundle["entry"]}
    assert {"Patient", "Composition"} <= types


ALL_CCDA_SAMPLES = sorted(
    p.name
    for p in (Path(__file__).resolve().parents[2] / "data" / "sample" / "ccda").iterdir()
    if p.suffix in (".ccda", ".xml")
)


@pytest.mark.parametrize("sample", ALL_CCDA_SAMPLES)
def test_every_ccda_sample_converts_with_ccd_template(api, samples, sample):
    """CcdaRenderer handles every synthetic CCDA sample shipped in data/sample/ccda."""
    r = api.post("/convert/ccda/CCD", data=(samples / "ccda" / sample).read_bytes(), headers=XML)
    assert r.ok, r.text()[:300]
    bundle = r.json()
    assert bundle["resourceType"] == "Bundle"
    assert bundle["type"] == "batch"
    types = {e["resource"]["resourceType"] for e in bundle["entry"]}
    assert {"Patient", "Composition"} <= types


def test_ccda_utf8_bom_is_accepted(api, samples):
    data = b"\xef\xbb\xbf" + (samples / "ccda" / "CCD.ccda").read_bytes()
    assert api.post("/convert/ccda/CCD", data=data, headers=XML).ok


def test_ccda_unknown_template_lists_available(api, samples):
    r = api.post("/convert/ccda/NoSuchTemplate", data=(samples / "ccda" / "CCD.ccda").read_bytes(), headers=XML)
    assert r.status == 404
    detail = r.json()["detail"]
    assert "NoSuchTemplate" in detail["error"]
    assert "CCD" in detail["available"]


@pytest.mark.parametrize("body", [b"", b"   \n  "])
def test_ccda_empty_body_is_400(api, body):
    assert api.post("/convert/ccda/CCD", data=body, headers=XML).status == 400


def test_ccda_non_utf8_body_is_400(api):
    assert api.post("/convert/ccda/CCD", data="<ClinicalDocument>é</ClinicalDocument>".encode("latin-1"), headers=XML).status == 400


def test_ccda_malformed_xml_is_422(api):
    """RenderingError from CcdaRenderer._parse_cda is mapped to 422 by _render."""
    r = api.post("/convert/ccda/CCD", data=b"<ClinicalDocument><unclosed>", headers=XML)
    assert r.status == 422, r.text()[:200]
    assert r.json()["detail"].startswith("Conversion failed")


def test_ccda_error_messages(api):
    assert api.post("/convert/ccda/CCD", data=b"", headers=XML).json()["detail"] == "Request body is empty"
    r = api.post("/convert/ccda/CCD", data="<a>é</a>".encode("latin-1"), headers=XML)
    assert r.json()["detail"] == "Payload must be UTF-8 encoded"


# --------------------------------------------------------------------------- #
# STU3 -> R4
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("resource", STU3_RESOURCES)
def test_stu3_infers_template_from_resource_type(api, samples, resource):
    r = api.post("/convert/stu3", data=(samples / "stu3" / f"{resource}.json").read_bytes(), headers=JSON)
    assert r.ok, r.text()
    assert r.headers["content-type"].startswith("application/fhir+json")
    assert r.json()["resourceType"] == resource


def test_stu3_explicit_template_query(api, samples):
    r = api.post("/convert/stu3?template=Patient", data=(samples / "stu3" / "Patient.json").read_bytes(), headers=JSON)
    assert r.ok
    assert r.json()["resourceType"] == "Patient"


def test_stu3_unknown_explicit_template_is_404(api, samples):
    r = api.post("/convert/stu3?template=Nope", data=(samples / "stu3" / "Patient.json").read_bytes(), headers=JSON)
    assert r.status == 404


@pytest.mark.parametrize(
    "body",
    [
        b"not json",
        b"[1, 2, 3]",
        json.dumps({"id": "x"}).encode(),
    ],
    ids=["invalid-json", "json-array", "missing-resourceType"],
)
def test_stu3_bad_payload_is_400(api, body):
    assert api.post("/convert/stu3", data=body, headers=JSON).status == 400


def test_stu3_empty_body_is_400(api):
    assert api.post("/convert/stu3", data=b"", headers=JSON).status == 400


def test_stu3_empty_body_with_explicit_template_is_400(api):
    r = api.post("/convert/stu3?template=Patient", data=b"", headers=JSON)
    assert r.status == 400
    assert r.json()["detail"] == "Request body is empty"


def test_stu3_empty_body_without_template_message(api):
    """With no template, _stu3_template_from sniffs the body first, so an empty body
    is rejected as invalid JSON before _render's empty-body check runs."""
    r = api.post("/convert/stu3", data=b"", headers=JSON)
    assert r.status == 400
    assert r.json()["detail"] == "Body is not a valid JSON object"


def test_stu3_non_utf8_with_explicit_template_message(api):
    """Explicit template skips sniffing, so _render's UnicodeDecodeError branch answers."""
    body = '{"resourceType": "Patient", "id": "é"}'.encode("latin-1")
    r = api.post("/convert/stu3?template=Patient", data=body, headers=JSON)
    assert r.status == 400
    assert r.json()["detail"] == "Payload must be UTF-8 encoded"


def test_stu3_missing_resource_type_message(api):
    r = api.post("/convert/stu3", data=json.dumps({"id": "x"}).encode(), headers=JSON)
    assert "resourceType" in r.json()["detail"]


@pytest.mark.parametrize("query", ["", "?template=Patient"], ids=["inferred", "explicit"])
def test_stu3_non_utf8_body_is_400(api, query):
    body = '{"resourceType": "Patient", "id": "é"}'.encode("latin-1")
    assert api.post(f"/convert/stu3{query}", data=body, headers=JSON).status == 400


def test_stu3_unknown_resource_type_is_404(api):
    r = api.post("/convert/stu3", data=json.dumps({"resourceType": "NoSuchResource"}).encode(), headers=JSON)
    assert r.status == 404
    assert "Patient" in r.json()["detail"]["available"]


def test_stu3_utf8_bom_is_accepted(api, samples):
    data = b"\xef\xbb\xbf" + (samples / "stu3" / "Patient.json").read_bytes()
    r = api.post("/convert/stu3", data=data, headers=JSON)
    assert r.ok, r.text()
    assert r.json()["resourceType"] == "Patient"


def test_stu3_preserves_resource_id(api, samples):
    src = json.loads((samples / "stu3" / "Observation.json").read_text(encoding="utf-8"))
    r = api.post("/convert/stu3", data=json.dumps(src).encode(), headers=JSON)
    assert r.json().get("id") == src.get("id")


@pytest.mark.parametrize("body", [b"null", b'"a string"', b"42"], ids=["null", "string", "number"])
def test_stu3_non_object_json_is_400(api, body):
    """_stu3_template_from: valid JSON that is not an object has no .get() (AttributeError branch)."""
    r = api.post("/convert/stu3", data=body, headers=JSON)
    assert r.status == 400
    assert r.json()["detail"] == "Body is not a valid JSON object"


def test_stu3_invalid_json_with_explicit_template_is_422(api):
    """Explicit template skips JSON sniffing, so Stu3FhirRenderer._parse_stu3 raises
    RenderingError inside render_to_fhir, which _render maps to 422."""
    r = api.post("/convert/stu3?template=Patient", data=b"{not json", headers=JSON)
    assert r.status == 422, r.text()[:200]
    assert r.json()["detail"].startswith("Conversion failed")


def test_stu3_explicit_template_overrides_resource_type(api, samples):
    """The template query param wins over the payload resourceType."""
    body = (samples / "stu3" / "Patient.json").read_bytes()
    r = api.post("/convert/stu3?template=NoSuchResource", data=body, headers=JSON)
    assert r.status == 404
    assert "NoSuchResource" in r.json()["detail"]["error"]


def test_stu3_resource_type_path_traversal_is_rejected(api):
    """A resourceType that looks like a path must not escape the stu3 template dir."""
    r = api.post("/convert/stu3", data=b'{"resourceType": "../ccda/CCD"}', headers=JSON)
    assert r.status == 404


def test_error_responses_are_plain_json(api):
    """Only successful conversions are labelled application/fhir+json."""
    r = api.post("/convert/ccda/CCD", data=b"", headers=XML)
    assert r.headers["content-type"].startswith("application/json")


def test_ccda_conversion_is_deterministic(api, samples):
    """Repeated calls reuse the cached CcdaRenderer and yield identical, hash-derived ids."""
    data = (samples / "ccda" / "CCD.ccda").read_bytes()
    first = api.post("/convert/ccda/CCD", data=data, headers=XML).json()
    second = api.post("/convert/ccda/CCD", data=data, headers=XML).json()
    assert first == second


@pytest.mark.xfail(
    strict=True,
    reason="APP BUG: fhir_converter/templates/stu3/Bundle.liquid (or a partial it includes) "
           "raises LiquidSyntaxError \"found an 'else' tag expression\", so every STU3 "
           "Bundle returns 422 'Conversion failed'",
)
def test_stu3_bundle_converts(api, samples):
    """Stu3FhirRenderer on a collection Bundle built from the synthetic Patient + Observation samples."""
    entries = [
        {"fullUrl": f"urn:uuid:{name}", "resource": json.loads((samples / "stu3" / f"{name}.json").read_text(encoding="utf-8"))}
        for name in ("Patient", "Observation")
    ]
    bundle = {"resourceType": "Bundle", "id": "e2e-bundle", "type": "collection", "entry": entries}
    r = api.post("/convert/stu3", data=json.dumps(bundle).encode(), headers=JSON)
    assert r.ok, r.text()[:300]
    out = r.json()
    assert out["resourceType"] == "Bundle"
    assert [e["resource"]["resourceType"] for e in out["entry"]] == ["Patient", "Observation"]


@pytest.mark.parametrize("resource", ["Patient", "Observation", "Procedure"])
def test_stu3_generic_resource_template_dispatches(api, samples, resource):
    """The 'Resource' template switches on msg.resourceType, so it must match the specific template."""
    data = (samples / "stu3" / f"{resource}.json").read_bytes()
    generic = api.post("/convert/stu3?template=Resource", data=data, headers=JSON)
    specific = api.post(f"/convert/stu3?template={resource}", data=data, headers=JSON)
    assert generic.ok, generic.text()[:300]
    assert generic.json() == specific.json()


@pytest.mark.xfail(
    strict=True,
    reason="APP BUG: list_templates globs every *.liquid in templates/ccda, so the "
           "'Header' include partial is advertised as a root template and converting "
           "with it returns 422 'Conversion failed'",
)
def test_every_listed_ccda_template_converts(api, samples):
    data = (samples / "ccda" / "CCD.ccda").read_bytes()
    failures = {}
    for name in api.get("/templates/ccda").json()["templates"]:
        r = api.post(f"/convert/ccda/{name}", data=data, headers=XML)
        if not r.ok:
            failures[name] = r.status
    assert failures == {}
