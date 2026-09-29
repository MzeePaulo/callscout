"""Generates the fixture PDFs used by test_pipeline.py. Run once:
    python tests/fixtures/make_pdfs.py
"""
from pathlib import Path

from fpdf import FPDF

HERE = Path(__file__).parent


def make(path: Path, lines: list[str]) -> None:
    pdf = FPDF()
    pdf.add_page()
    pdf.set_font("Helvetica", size=12)
    for line in lines:
        pdf.multi_cell(0, 8, line if line.strip() else " ", new_x="LMARGIN", new_y="NEXT")
    pdf.output(str(path))


if __name__ == "__main__":
    (HERE / "files").mkdir(exist_ok=True)
    make(
        HERE / "files" / "convergence-guidelines.pdf",
        [
            "Convergence Blended Finance Facility - Full Guidelines",
            "",
            "Section 3: Key dates and funding",
            "Application deadline: 2026-05-15",
            "Grant size: $50,000 - $200,000",
        ],
    )
    make(
        HERE / "files" / "rfp-guidelines.pdf",
        [
            "DFI Catalytic Capital RFP - Guidelines",
            "",
            "Timeline",
            "The call opens on 1 February 2026 and the submission deadline is 2026-06-01.",
            "",
            "Funding",
            "Awards of up to $500,000 are available per selected proposal.",
        ],
    )
    make(
        HERE / "files" / "own-guidelines.pdf",
        [
            "Climate Innovation Window - Additional Guidelines",
            "",
            "Eligibility criteria apply; see the main call page for the deadline.",
            "Grant ceiling: up to $75,000 per selected applicant.",
        ],
    )
    print("wrote fixture PDFs to", HERE / "files")
