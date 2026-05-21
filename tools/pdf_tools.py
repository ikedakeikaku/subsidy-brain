"""PDF読み取り・分割・結合ユーティリティ

pdfplumber によるテキスト抽出と pypdf によるページ操作を提供する。
"""

import base64
import io
import logging

import pdfplumber
from pypdf import PdfReader, PdfWriter

logger = logging.getLogger(__name__)


def extract_text_from_pdf(pdf_data: bytes) -> list[dict]:
    """PDFからページごとにテキストを抽出する。

    Args:
        pdf_data: PDFファイルのバイナリデータ。

    Returns:
        ページ番号（1始まり）とテキストの辞書のリスト。
        例: [{"page": 1, "text": "..."}, {"page": 2, "text": "..."}, ...]
    """
    pages: list[dict] = []
    with pdfplumber.open(io.BytesIO(pdf_data)) as pdf:
        for i, page in enumerate(pdf.pages, start=1):
            text = page.extract_text() or ""
            pages.append({"page": i, "text": text})
            logger.debug("ページ %d: %d 文字抽出", i, len(text))
    logger.info("PDF テキスト抽出完了: %d ページ", len(pages))
    return pages


def extract_pages(pdf_data: bytes, pages: list[int]) -> bytes:
    """指定ページを抽出して新しいPDFとして返す。

    Args:
        pdf_data: 元PDFのバイナリデータ。
        pages: 抽出するページ番号のリスト（1始まり）。

    Returns:
        抽出されたページのみを含むPDFのバイナリデータ。

    Raises:
        ValueError: 指定ページ番号が範囲外の場合。
    """
    reader = PdfReader(io.BytesIO(pdf_data))
    total_pages = len(reader.pages)

    writer = PdfWriter()
    for page_num in pages:
        if page_num < 1 or page_num > total_pages:
            raise ValueError(
                f"ページ番号 {page_num} は範囲外です（全 {total_pages} ページ）"
            )
        writer.add_page(reader.pages[page_num - 1])

    buffer = io.BytesIO()
    writer.write(buffer)
    buffer.seek(0)
    logger.info("ページ抽出完了: %s （全 %d ページ中）", pages, total_pages)
    return buffer.read()


def merge_pdfs(pdf_list: list[bytes]) -> bytes:
    """複数のPDFを1つに結合する。

    Args:
        pdf_list: 結合するPDFバイナリデータのリスト（先頭から順に結合）。

    Returns:
        結合されたPDFのバイナリデータ。

    Raises:
        ValueError: 空リストが渡された場合。
    """
    if not pdf_list:
        raise ValueError("結合するPDFが指定されていません")

    writer = PdfWriter()
    for i, pdf_data in enumerate(pdf_list):
        reader = PdfReader(io.BytesIO(pdf_data))
        for page in reader.pages:
            writer.add_page(page)
        logger.debug("PDF %d: %d ページ追加", i + 1, len(reader.pages))

    buffer = io.BytesIO()
    writer.write(buffer)
    buffer.seek(0)
    total = len(writer.pages)
    logger.info("PDF結合完了: %d ファイル → %d ページ", len(pdf_list), total)
    return buffer.read()


def get_page_count(pdf_data: bytes) -> int:
    """PDFのページ数を返す。

    Args:
        pdf_data: PDFファイルのバイナリデータ。

    Returns:
        ページ数。
    """
    reader = PdfReader(io.BytesIO(pdf_data))
    return len(reader.pages)


async def identify_pages(pdf_data: bytes, categories: dict | None = None) -> list[dict]:
    """PDFの各ページの内容をClaude APIで判定し、ページ番号と書類種別のマッピングを返す。

    Args:
        pdf_data: PDFファイルのバイナリデータ。
        categories: 書類分類カテゴリの辞書（Noneの場合はデフォルト分類を使用）。

    Returns:
        [
            {"page": 1, "category": "balance_sheet", "confidence": 0.95},
            {"page": 2, "category": "balance_sheet", "confidence": 0.92},
            ...
        ]
    """
    from tools.claude_client import call_claude, parse_json_response

    pages = extract_text_from_pdf(pdf_data)
    results = []

    # カテゴリのキーワードマッピング（デフォルト）
    if categories is None:
        from agents.a15_attachment import DOCUMENT_CATEGORIES
        categories = DOCUMENT_CATEGORIES

    category_descriptions = "\n".join(
        f"- {key}: {cat['label']} (キーワード: {', '.join(cat['keywords'])})"
        for key, cat in categories.items()
    )

    for page_info in pages:
        page_num = page_info["page"]
        text = page_info["text"]

        if not text.strip():
            results.append({"page": page_num, "category": "unknown", "confidence": 0.0})
            continue

        # テキストが短すぎる場合はスキップ
        if len(text.strip()) < 10:
            results.append({"page": page_num, "category": "unknown", "confidence": 0.1})
            continue

        prompt = f"""以下のテキストは書類PDFの1ページ分です。書類の種別を判定してください。

分類カテゴリ:
{category_descriptions}

テキスト（先頭2000文字）:
{text[:2000]}

以下のJSON形式で回答してください:
{{"category": "カテゴリキー", "confidence": 0.0〜1.0の数値}}
"""
        try:
            response = await call_claude(
                system_prompt="書類分類の専門家です。PDFページのテキストから書類種別を判定します。",
                user_message=prompt,
                temperature=0.1,
            )
            parsed = parse_json_response(response)
            results.append({
                "page": page_num,
                "category": parsed.get("category", "unknown"),
                "confidence": float(parsed.get("confidence", 0.0)),
            })
        except Exception as e:
            logger.warning("ページ %d の種別判定に失敗: %s", page_num, e)
            results.append({"page": page_num, "category": "unknown", "confidence": 0.0})

    logger.info("ページ種別判定完了: %d ページ", len(results))
    return results


def extract_pages_to_file(pdf_data: bytes, page_numbers: list[int], output_path: str) -> str:
    """指定ページを抽出して新しいPDFファイルとして保存する。

    Args:
        pdf_data: 元PDFのバイナリデータ。
        page_numbers: 抽出するページ番号のリスト（1始まり）。
        output_path: 出力先ファイルパス。

    Returns:
        出力ファイルパス。
    """
    extracted = extract_pages(pdf_data, page_numbers)
    with open(output_path, "wb") as f:
        f.write(extracted)
    logger.info("ページ抽出・保存: %s (%d ページ)", output_path, len(page_numbers))
    return output_path


def decode_base64_pdf(b64_string: str) -> bytes:
    """Base64エンコードされたPDFをデコードする。

    Args:
        b64_string: Base64文字列。

    Returns:
        デコードされたPDFバイナリデータ。

    Raises:
        ValueError: デコードに失敗した場合。
    """
    try:
        return base64.b64decode(b64_string)
    except Exception as exc:
        raise ValueError("Base64のデコードに失敗しました") from exc
