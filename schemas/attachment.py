"""#15 添付書類 入出力スキーマ

添付書類のPDF分割・命名・内容検証を行う。
"""

from __future__ import annotations

from pydantic import BaseModel, Field

# ============================================================
# Input supporting models
# ============================================================


class ExtractionRule(BaseModel):
    """PDF抽出ルール"""

    pages: list[int] = Field(default_factory=list, description="抽出対象ページ")
    section_name: str | None = Field(default=None, description="セクション名")
    required_fields: list[str] = Field(
        default_factory=list, description="必須フィールド一覧"
    )


class RequiredAttachment(BaseModel):
    """必要な添付書類の定義"""

    doc_id: str = Field(..., description="書類ID")
    doc_type: str = Field(
        ...,
        description="pl_extract | bs_extract | tax_return | wage_ledger | estimate | id_copy | other",
    )
    source_file: str = Field(
        ..., description="元ファイル（Google Drive URLまたはBase64）"
    )
    extraction_rule: ExtractionRule = Field(
        default_factory=ExtractionRule, description="抽出ルール"
    )


class NamingConvention(BaseModel):
    """ファイル命名規則"""

    prefix: str = Field(default="", description="接頭辞（第XX回_事業者名）")
    format: str = Field(
        default="【{prefix}】{doc_type_label}_{serial}",
        description="命名フォーマット",
    )


# ============================================================
# Input
# ============================================================


class AttachmentInput(BaseModel):
    """#15 添付書類入力"""

    applicant_id: str = Field(..., description="申請者ID")
    required_attachments: list[RequiredAttachment] = Field(
        default_factory=list, description="必要添付書類一覧"
    )
    naming_convention: NamingConvention = Field(
        default_factory=NamingConvention, description="命名規則"
    )


# ============================================================
# Output supporting models
# ============================================================


class ContentCheck(BaseModel):
    """内容チェックの個別項目"""

    field: str = Field(..., description="チェック対象フィールド")
    expected: str = Field(default="", description="期待値")
    found: str = Field(default="", description="検出値")
    match: bool = Field(default=False, description="一致したか")


class ContentValidation(BaseModel):
    """書類の内容検証結果"""

    status: str = Field(
        default="verified", description="verified | warning | manual_required"
    )
    checks: list[ContentCheck] = Field(
        default_factory=list, description="個別チェック結果"
    )
    warnings: list[str] = Field(default_factory=list, description="警告一覧")


class GeneratedFile(BaseModel):
    """生成された添付書類ファイル"""

    doc_id: str = Field(..., description="書類ID")
    doc_type: str = Field(..., description="書類種別")
    file_name: str = Field(..., description="ファイル名")
    file_path: str = Field(default="", description="Google Drive URL")
    page_count: int = Field(default=0, description="ページ数")
    content_validation: ContentValidation = Field(
        default_factory=ContentValidation, description="内容検証結果"
    )
    file_size_kb: float = Field(default=0, description="ファイルサイズ（KB）")


class AttachmentSummary(BaseModel):
    """添付書類生成サマリー"""

    total_required: int = Field(default=0, description="必要書類数")
    total_generated: int = Field(default=0, description="生成済み書類数")
    total_verified: int = Field(default=0, description="検証済み書類数")
    missing: list[str] = Field(default_factory=list, description="未生成の書類")
    ready_for_submission: bool = Field(
        default=False, description="提出可能な状態か"
    )


# ============================================================
# Output
# ============================================================


class AttachmentOutput(BaseModel):
    """#15 添付書類出力"""

    generated_files: list[GeneratedFile] = Field(
        default_factory=list, description="生成ファイル一覧"
    )
    summary: AttachmentSummary = Field(
        default_factory=AttachmentSummary, description="サマリー"
    )
    drive_folder_url: str = Field(
        default="", description="Google Driveフォルダ URL"
    )
