"""OCRユーティリティ

Claude Vision APIを使用した画像・PDFテキスト認識を提供する。
pdfplumber でテキスト抽出を試み、不十分な場合は Vision API にフォールバックする。
"""

import logging

from tools.claude_client import call_claude_vision
from tools.pdf_tools import extract_pages, extract_text_from_pdf

logger = logging.getLogger(__name__)

# pdfplumber で取得したテキストがこの文字数未満の場合、OCRにフォールバック
_MIN_TEXT_LENGTH = 50

_OCR_SYSTEM_PROMPT = (
    "あなたは高精度OCRエンジンです。画像またはPDFに含まれるすべてのテキストを"
    "正確に読み取り、元のレイアウトを可能な限り保持して出力してください。"
    "表がある場合はMarkdown形式の表として再現してください。"
    "読み取れない文字は [判読不能] と記載してください。"
)


async def ocr_image(image_data: bytes, media_type: str) -> str:
    """画像データからテキストをOCR抽出する。

    Args:
        image_data: 画像のバイナリデータ。
        media_type: MIMEタイプ（例: "image/png", "image/jpeg", "image/webp"）。

    Returns:
        抽出されたテキスト。
    """
    logger.info("画像OCR開始 (media_type=%s, size=%d bytes)", media_type, len(image_data))
    text = await call_claude_vision(
        system_prompt=_OCR_SYSTEM_PROMPT,
        image_data=image_data,
        media_type=media_type,
        text_prompt="この画像に含まれるすべてのテキストを読み取ってください。",
    )
    logger.info("画像OCR完了: %d 文字抽出", len(text))
    return text


async def ocr_pdf_page(pdf_data: bytes, page_number: int) -> str:
    """PDFの指定ページをOCRでテキスト抽出する。

    ページを単独PDFとして抽出し、Claude Vision APIに送信する。

    Args:
        pdf_data: PDFファイルのバイナリデータ。
        page_number: OCRするページ番号（1始まり）。

    Returns:
        抽出されたテキスト。
    """
    logger.info("PDF OCR開始: ページ %d", page_number)
    single_page_pdf = extract_pages(pdf_data, [page_number])

    text = await call_claude_vision(
        system_prompt=_OCR_SYSTEM_PROMPT,
        image_data=single_page_pdf,
        media_type="application/pdf",
        text_prompt=f"このPDF（ページ {page_number}）に含まれるすべてのテキストを読み取ってください。",
    )
    logger.info("PDF OCR完了 (ページ %d): %d 文字抽出", page_number, len(text))
    return text


async def ocr_document(file_data: bytes, source_type: str) -> str:
    """ファイル種別に応じてテキスト抽出を行うディスパッチャー。

    - 画像ファイル（image/*）: Claude Vision で直接OCR
    - PDF: まず pdfplumber でテキスト抽出を試み、テキストが短すぎる場合は
      ページごとに OCR にフォールバック

    Args:
        file_data: ファイルのバイナリデータ。
        source_type: ファイル種別。"image/png", "image/jpeg", "image/webp",
                     "pdf", "application/pdf" など。

    Returns:
        抽出されたテキスト全体。
    """
    # 画像の場合
    if source_type.startswith("image/"):
        return await ocr_image(file_data, media_type=source_type)

    # PDFの場合
    if source_type in ("pdf", "application/pdf"):
        # まず pdfplumber でテキスト抽出を試行
        pages = extract_text_from_pdf(file_data)
        all_text = "\n\n".join(
            f"--- ページ {p['page']} ---\n{p['text']}" for p in pages
        )

        # テキストが十分にある場合はそのまま返す
        total_chars = sum(len(p["text"]) for p in pages)
        if total_chars >= _MIN_TEXT_LENGTH:
            logger.info(
                "pdfplumber で %d 文字抽出済み。OCRフォールバック不要。", total_chars
            )
            return all_text

        # テキストが少ない（スキャンPDFの可能性）→ 全ページをOCR
        logger.info(
            "pdfplumber テキスト不足 (%d 文字)。OCRフォールバックを実行。",
            total_chars,
        )
        ocr_parts: list[str] = []
        for page_info in pages:
            page_text = await ocr_pdf_page(file_data, page_info["page"])
            ocr_parts.append(f"--- ページ {page_info['page']} ---\n{page_text}")

        return "\n\n".join(ocr_parts)

    raise ValueError(f"未対応のファイル種別です: {source_type}")
