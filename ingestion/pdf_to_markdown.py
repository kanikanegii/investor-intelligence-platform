from pathlib import Path

import pymupdf4llm

from ingestion.schemas import PageMarkdown


class PDFToMarkdownConverter:
    """Convert PDF documents to Markdown."""

    def convert_pdf_pages(self, pdf_path: str) -> list[PageMarkdown]:
        """
        Convert a PDF to per-page markdown, preserving page numbers.

        Args:
            pdf_path: Source PDF path.

        Returns:
            One PageMarkdown per non-empty page (1-indexed page_number).
        """
        pdf_file = Path(pdf_path)

        if not pdf_file.exists():
            raise FileNotFoundError(f"PDF file not found: {pdf_path}")

        pages = pymupdf4llm.to_markdown(str(pdf_file), page_chunks=True)

        return [
            PageMarkdown(page_number=page["metadata"]["page_number"], text=page["text"])
            for page in pages
            if page["text"].strip()
        ]

    def convert_pdf(self, pdf_path: str, output_dir: str) -> str:
        """
        Convert a PDF document to a single Markdown file for human inspection.

        Args:
            pdf_path: Source PDF path.
            output_dir: Output markdown directory.

        Returns:
            Generated markdown file path.
        """
        pdf_file = Path(pdf_path)
        pages = self.convert_pdf_pages(pdf_path)

        output_path = Path(output_dir)
        output_path.mkdir(parents=True, exist_ok=True)

        markdown_content = "\n\n".join(page.text for page in pages)

        markdown_file = output_path / f"{pdf_file.stem}.md"

        markdown_file.write_text(
            markdown_content,
            encoding="utf-8"
        )

        return str(markdown_file)

    def convert_directory(self, input_dir: str, output_dir: str) -> list[str]:
        """
        Convert all PDFs from a directory to Markdown.

        Args:
            input_dir: Directory containing PDF files.
            output_dir: Directory to save markdown files.

        Returns:
            List of generated markdown file paths.
        """
        input_path = Path(input_dir)
        markdown_files = []

        for pdf_file in input_path.glob("*.pdf"):
            markdown_file = self.convert_pdf(
                pdf_path=str(pdf_file),
                output_dir=output_dir
            )

            markdown_files.append(markdown_file)

        return markdown_files
