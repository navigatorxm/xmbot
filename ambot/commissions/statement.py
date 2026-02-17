"""
PDFStatementGenerator — generates monthly commission statements.

Uses ReportLab for PDF generation (no system dependencies, pure Python).
WeasyPrint (HTML→PDF) is the fallback for richer styling.

Output: data/statements/{client_id}/{year}-{month:02d}.pdf
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from decimal import Decimal

from ambot.commissions.calculator import CommissionResult

log = logging.getLogger("ambot.commissions.statement")


class PDFStatementGenerator:
    def __init__(self, output_dir: str = "data/statements") -> None:
        self.output_dir = output_dir

    async def generate(self, result: CommissionResult) -> str | None:
        """
        Generate a PDF statement for a commission result.

        Returns the file path of the generated PDF, or None on failure.
        """
        try:
            return self._generate_pdf(result)
        except ImportError:
            log.warning("ReportLab not available — skipping PDF generation")
            return None
        except Exception as exc:
            log.error("PDF generation failed for client=%s: %s", result.client_id, exc)
            return None

    def _generate_pdf(self, result: CommissionResult) -> str:
        from reportlab.lib import colors
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.styles import getSampleStyleSheet
        from reportlab.lib.units import cm
        from reportlab.platypus import (
            Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
        )

        # Create output directory
        client_dir = os.path.join(self.output_dir, result.client_id)
        os.makedirs(client_dir, exist_ok=True)

        filename = f"{result.period_start.year}-{result.period_start.month:02d}.pdf"
        filepath = os.path.join(client_dir, filename)

        doc = SimpleDocTemplate(
            filepath,
            pagesize=A4,
            rightMargin=2 * cm,
            leftMargin=2 * cm,
            topMargin=2 * cm,
            bottomMargin=2 * cm,
        )

        styles = getSampleStyleSheet()
        elements = []

        # Title
        elements.append(Paragraph("xmbot — Monthly Commission Statement", styles["Title"]))
        elements.append(Spacer(1, 0.5 * cm))

        # Period
        elements.append(Paragraph(
            f"Period: {result.period_start} → {result.period_end}",
            styles["Normal"],
        ))
        elements.append(Paragraph(
            f"Client ID: {result.client_id}",
            styles["Normal"],
        ))
        elements.append(Paragraph(
            f"Generated: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}",
            styles["Normal"],
        ))
        elements.append(Spacer(1, 0.5 * cm))

        # Summary table
        def fmt(d: Decimal) -> str:
            return f"${float(d):,.2f}"

        table_data = [
            ["Item", "Amount"],
            ["Starting Balance", fmt(result.starting_balance)],
            ["Ending Balance", fmt(result.ending_balance)],
            ["Net Deposits / Withdrawals", fmt(result.net_deposits)],
            ["High Watermark (before)", fmt(result.high_watermark_before)],
            ["High Watermark (after)", fmt(result.high_watermark_after)],
            ["", ""],
            ["Monthly AUM Fee (1%)", fmt(result.monthly_fee)],
            [f"Performance ({fmt(result.performance)} × 20%)", fmt(result.performance_fee)],
            ["TOTAL COMMISSION DUE", fmt(result.total_commission)],
        ]

        table = Table(table_data, colWidths=[10 * cm, 5 * cm])
        table.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1a1a2e")),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("FONTSIZE", (0, 0), (-1, 0), 11),
            ("ALIGN", (1, 0), (1, -1), "RIGHT"),
            ("BACKGROUND", (0, -1), (-1, -1), colors.HexColor("#e8f4f8")),
            ("FONTNAME", (0, -1), (-1, -1), "Helvetica-Bold"),
            ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
            ("ROWBACKGROUNDS", (0, 1), (-1, -2), [colors.white, colors.HexColor("#f5f5f5")]),
            ("TOPPADDING", (0, 0), (-1, -1), 6),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
        ]))
        elements.append(table)
        elements.append(Spacer(1, 1 * cm))

        # Disclaimer
        elements.append(Paragraph(
            "<i>This statement is generated automatically. "
            "All figures are based on Binance account data. "
            "No guaranteed returns. Trading involves risk.</i>",
            styles["Small"],
        ))

        doc.build(elements)
        log.info("PDF statement generated: %s", filepath)
        return filepath
