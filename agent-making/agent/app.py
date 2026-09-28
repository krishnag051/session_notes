"""Phase 1 + Phase 2 verification wrapper.

Tab 1 (Phase 1): visually confirm the batch classifier's split against a
real PDF. Tab 2 (Phase 2): pick one classified person/document and run the
actual rule-checking pipeline against it.

Real-API cost discipline (CLAUDE.md's hard rule): the review tab defaults
to a fully MOCKED model boundary — no real key required, zero cost, fully
clickable and demoable. A real Anthropic call only happens if you tick
BOTH "use the real Anthropic API" AND the explicit confirmation checkbox
below it, and only after you've added your own key to agent-making/.env —
this app never asks you for one, and never runs a real call on its own.
"""
import sys
import tempfile
from pathlib import Path

import streamlit as st
from pypdf import PdfReader, PdfWriter

# Streamlit only ever adds this script's OWN directory to sys.path, never its
# parent — so `import agent.pipeline...` (an absolute import, needed because
# Streamlit executes this file as a top-level script, not as part of the
# `agent` package) only resolves if agent-making/ is on sys.path too. Insert
# it explicitly so this works regardless of which directory `streamlit run`
# is invoked from.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent.pipeline import judge as judge_module
from agent.pipeline.api import classify_batch_pdf, review_person_document
from agent.pipeline.model_boundary import resolve_judgment_boundary

st.set_page_config(page_title="Session Note Compliance — agent-making", layout="wide")
st.title("Session Note Compliance — agent-making")


def _mock_run_judgment_checks_once(judgment_rules, fields, rendered_images, tracker=None, call_reason="call", model_override=None):
    return {
        r["rule_id"]: {
            "result": "pass",
            "evidence": (
                "Mocked judgment result (demo mode) — no real model call was made. Tick "
                "'Use the real Anthropic API' and confirm below to get a real answer."
            ),
            "page": 1,
            "confidence": 0.0,
        }
        for r in judgment_rules
    }


def _slice_pdf_to_temp_file(src_path: str, page_start: int, page_end: int) -> str:
    reader = PdfReader(src_path)
    writer = PdfWriter()
    for i in range(page_start - 1, page_end):
        writer.add_page(reader.pages[i])
    tmp = tempfile.NamedTemporaryFile(suffix=".pdf", delete=False)
    writer.write(tmp)
    tmp.close()
    return tmp.name


_RESULT_TO_GROUP = {
    "fail": "Failed",
    "uncertain": "Failed",
    "not_applicable": "Not Applicable",
    "not_checkable": "Not Applicable",
    "pass": "Passed",
}
GROUPS = ("Failed", "Not Applicable", "Informational", "Passed")


def _finding_group(entry: dict) -> str:
    if entry.get("type") == "Informational":
        return "Informational"
    return _RESULT_TO_GROUP.get(entry.get("result"), "Failed")


tab_classify, tab_review = st.tabs(["1 — Classify batch", "2 — Review a document"])

with tab_classify:
    st.caption(
        "Uploads a batch PDF, splits it into per-person documents with zero model calls, "
        "and shows the result for a manual visual check against the source PDF."
    )
    uploaded = st.file_uploader("Batch PDF", type="pdf", key="batch_uploader")

    if uploaded is not None:
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
            tmp.write(uploaded.getvalue())
            tmp_path = tmp.name

        classification = classify_batch_pdf(tmp_path)
        # Kept for tab 2 — the review tab slices individual documents out
        # of this SAME uploaded PDF, so both the classification result and
        # the original file's own temp path need to survive the rerun that
        # switching tabs triggers.
        st.session_state["classification_result"] = classification
        st.session_state["classified_pdf_path"] = tmp_path

        people = classification["people"]
        admin_noise_pages = classification["admin_noise_pages"]
        unresolved = classification["unresolved"]

        st.subheader(f"People found — {len(people)}")
        st.dataframe(
            [
                {
                    "Name": p["full_name"],
                    "DOB": p["dob"],
                    "global_key": p["global_key"],
                    "Cover page": p["cover_page"],
                    "# documents": len(p["documents"]),
                }
                for p in people
            ],
            use_container_width=True,
            hide_index=True,
        )

        for person in people:
            with st.expander(f"{person['full_name']} — {len(person['documents'])} document(s)"):
                st.dataframe(
                    [
                        {
                            "Appendix": d["appendix"] or "—",
                            "Service code": d["service_code"],
                            "Date of service": d["date_of_service"],
                            "Pages": f"{d['page_start']}–{d['page_end']}",
                        }
                        for d in person["documents"]
                    ],
                    use_container_width=True,
                    hide_index=True,
                )

        st.divider()
        col_noise, col_unresolved = st.columns(2)
        with col_noise:
            st.subheader(f"⚪ Admin noise — excluded ({len(admin_noise_pages)})")
            st.caption("Cover pages with no billable service line — never counted as a real person.")
            if not admin_noise_pages:
                st.write("None found.")
            for noise in admin_noise_pages:
                st.warning(f"Pages {noise['page_start']}–{noise['page_end']}: {noise['reason']}")
        with col_unresolved:
            st.subheader(f"🔴 Unresolved — needs a human look ({len(unresolved)})")
            st.caption("A page range that didn't cleanly resolve — never silently guessed at.")
            if not unresolved:
                st.write("None found.")
            for item in unresolved:
                st.error(f"Pages {item['page_start']}–{item['page_end']}: {item['note']}")

        st.divider()
        with st.expander("Raw BatchClassificationResult (JSON)"):
            st.json(classification)
    else:
        st.info("Upload a batch PDF to classify it.")

with tab_review:
    classification = st.session_state.get("classification_result")
    pdf_path = st.session_state.get("classified_pdf_path")

    if classification is None or not classification["people"]:
        st.info("Classify a batch with at least one real person on tab 1 first.")
    else:
        people = classification["people"]
        person_labels = [f"{p['full_name']} ({p['global_key']})" for p in people]
        person_idx = st.selectbox("Person", range(len(people)), format_func=lambda i: person_labels[i])
        person = people[person_idx]

        if not person["documents"]:
            st.warning("This person has no documents.")
        else:
            doc_labels = [
                f"{d['service_code']} — Appendix {d['appendix'] or '—'} — {d['date_of_service']} (pages {d['page_start']}-{d['page_end']})"
                for d in person["documents"]
            ]
            doc_idx = st.selectbox("Document", range(len(person["documents"])), format_func=lambda i: doc_labels[i])
            doc = person["documents"][doc_idx]

            st.text_area(
                "Optional: paste a prior session's full text here to test the copy-paste / "
                "identical-data-point rules (leave blank to see them resolve to not_checkable)",
                key="prior_note_text", height=100,
            )

            use_real_api = st.checkbox(
                "Use the real Anthropic API (spends real money — requires ANTHROPIC_API_KEY in agent-making/.env)",
                value=False,
            )
            st.caption(
                "A real run is protected by a hard $4.00/session spend ceiling (env: MAX_REAL_SPEND_USD) — "
                "enforced before each call goes out, not after."
            )
            confirmed_real = False
            if use_real_api:
                confirmed_real = st.checkbox(
                    "I understand this will make real, billed Anthropic API calls, and I have explicit "
                    "per-instance approval to run it right now (CLAUDE.md's hard rule).",
                )
                if not confirmed_real:
                    st.warning("Confirm the checkbox above to actually use the real API — until then, this still runs mocked.")

            _mocked = resolve_judgment_boundary(
                judge_module, use_real_api=use_real_api, confirmed_real=confirmed_real,
                mock_fn=_mock_run_judgment_checks_once,
            )
            st.caption(
                f"Model boundary this run: {'MOCKED (demo mode)' if _mocked else '🔴 REAL — will make a billed Anthropic API call'}"
            )

            if st.button("Run review"):
                sliced_path = _slice_pdf_to_temp_file(pdf_path, doc["page_start"], doc["page_end"])
                prior_text = st.session_state.get("prior_note_text", "").strip()
                prior_extractions = (
                    [{"date_of_service": "unknown", "full_text": prior_text}] if prior_text else None
                )
                try:
                    result = review_person_document(
                        sliced_path, doc["service_code"], prior_extractions=prior_extractions,
                        model_override="openrouter" if _mocked else None,
                    )
                finally:
                    Path(sliced_path).unlink(missing_ok=True)

                if result.status == "error":
                    st.error(f"Review failed: {result.error}")
                else:
                    st.success(
                        f"Reviewed. {result.usage.api_calls} real API call(s), "
                        f"~${result.usage.estimated_cost_usd:.4f} estimated cost."
                    )
                    grouped: dict[str, list] = {g: [] for g in GROUPS}
                    for rule_id, entry in result.findings.items():
                        grouped[_finding_group(entry)].append((rule_id, entry))

                    for group in GROUPS:
                        items = grouped[group]
                        if not items:
                            continue
                        st.subheader(f"{group} ({len(items)})")
                        for rule_id, entry in items:
                            evidence = entry["evidence"]
                            evidence_text = evidence if isinstance(evidence, str) else "; ".join(
                                f"[p{i.get('page')}] {i.get('detail')}" for i in evidence if isinstance(i, dict)
                            )
                            st.markdown(f"**{rule_id}** ({entry['result']}) — {evidence_text}")

                    with st.expander("Raw findings (JSON)"):
                        st.json(result.to_dict())
