"""グラフ生成ユーティリティ

matplotlib を使用して収益推移グラフ・効果比較グラフを PNG として出力する。
日本語フォントを自動設定する。
グラフ生成はオプション機能のため、import 失敗時は警告のみでエラーにしない。
"""

import logging
from pathlib import Path

logger = logging.getLogger(__name__)

try:
    import matplotlib
    matplotlib.use("Agg")  # GUI 不要のバックエンド
    import matplotlib.pyplot as plt
    import numpy as np
    _MATPLOTLIB_AVAILABLE = True
except ImportError:
    _MATPLOTLIB_AVAILABLE = False
    logger.warning("matplotlib が見つかりません。グラフ生成機能は無効化されます。")


def _setup_japanese_font() -> None:
    """matplotlib で日本語テキストを表示するためのフォント設定。

    macOS では Hiragino Sans、Windows では MS Gothic、
    いずれも利用できない場合はデフォルトにフォールバックする。
    """
    if not _MATPLOTLIB_AVAILABLE:
        return

    import matplotlib.font_manager as fm

    candidate_fonts = [
        "Hiragino Sans",      # macOS
        "Hiragino Kaku Gothic ProN",  # macOS (代替)
        "MS Gothic",          # Windows
        "Noto Sans CJK JP",   # Linux
        "IPAexGothic",        # Linux (IPA フォント)
    ]

    available = {f.name for f in fm.fontManager.ttflist}
    for font_name in candidate_fonts:
        if font_name in available:
            matplotlib.rcParams["font.family"] = font_name
            logger.debug("日本語フォント設定: %s", font_name)
            return

    logger.warning(
        "日本語対応フォントが見つかりません。グラフのラベルが文字化けする可能性があります。"
    )


def generate_revenue_chart(
    years: list[str],
    revenues: list[float],
    output_path: str,
) -> str:
    """年度別売上推移の棒グラフを生成して PNG として保存する。

    Args:
        years: X 軸のラベルリスト（例: ["2021年度", "2022年度", "2023年度"]）。
        revenues: 各年度の売上金額リスト（単位: 円）。
        output_path: 出力先 PNG ファイルパス。

    Returns:
        保存した PNG ファイルの絶対パス。

    Raises:
        ImportError: matplotlib がインストールされていない場合。
        ValueError: years と revenues の長さが一致しない場合。
    """
    if not _MATPLOTLIB_AVAILABLE:
        raise ImportError(
            "matplotlib がインストールされていません。"
            " `pip install matplotlib` を実行してください。"
        )

    if len(years) != len(revenues):
        raise ValueError(
            f"years ({len(years)}件) と revenues ({len(revenues)}件) の長さが一致しません。"
        )

    try:
        _setup_japanese_font()

        revenues_man = [r / 10_000 for r in revenues]

        fig, ax = plt.subplots(figsize=(8, 5))
        bar_color = "#4472C4"  # Word 標準の青
        bars = ax.bar(years, revenues_man, color=bar_color, width=0.5, zorder=3)

        # 棒の上に数値ラベル
        for bar, val in zip(bars, revenues_man):
            ax.text(
                bar.get_x() + bar.get_width() / 2,
                bar.get_height() + max(revenues_man) * 0.01,
                f"{val:,.0f}",
                ha="center",
                va="bottom",
                fontsize=9,
            )

        ax.set_xlabel("年度", fontsize=10)
        ax.set_ylabel("売上高（万円）", fontsize=10)
        ax.set_title("年度別売上推移", fontsize=12, fontweight="bold")
        ax.yaxis.grid(True, linestyle="--", alpha=0.7, zorder=0)
        ax.set_axisbelow(True)
        ax.spines["top"].set_visible(False)
        ax.spines["right"].set_visible(False)

        plt.tight_layout()

        out = Path(output_path).resolve()
        out.parent.mkdir(parents=True, exist_ok=True)
        fig.savefig(str(out), dpi=150, bbox_inches="tight")
        plt.close(fig)

        logger.info("売上推移グラフ保存完了: %s", out)
        return str(out)

    except Exception as exc:
        logger.error("売上推移グラフ生成エラー: %s", exc, exc_info=True)
        raise


def generate_effect_chart(
    labels: list[str],
    current: list[float],
    projected: list[float],
    output_path: str,
) -> str:
    """現状値と予測値を並べた比較棒グラフを生成して PNG として保存する。

    Args:
        labels: 各指標のラベルリスト（例: ["売上高", "客単価", "来店客数"]）。
        current: 各指標の現状値リスト。
        projected: 各指標の予測値リスト。
        output_path: 出力先 PNG ファイルパス。

    Returns:
        保存した PNG ファイルの絶対パス。

    Raises:
        ImportError: matplotlib がインストールされていない場合。
        ValueError: labels / current / projected の長さが一致しない場合。
    """
    if not _MATPLOTLIB_AVAILABLE:
        raise ImportError(
            "matplotlib がインストールされていません。"
            " `pip install matplotlib` を実行してください。"
        )

    if not (len(labels) == len(current) == len(projected)):
        raise ValueError(
            "labels / current / projected のリスト長が一致しません。"
        )

    try:
        _setup_japanese_font()

        x = np.arange(len(labels))
        bar_width = 0.35
        color_current = "#4472C4"    # 青: 現状
        color_projected = "#ED7D31"  # オレンジ: 予測

        fig, ax = plt.subplots(figsize=(9, 5))

        bars_current = ax.bar(
            x - bar_width / 2, current,
            width=bar_width, label="現状", color=color_current, zorder=3,
        )
        bars_projected = ax.bar(
            x + bar_width / 2, projected,
            width=bar_width, label="補助事業後（予測）", color=color_projected, zorder=3,
        )

        # 棒の上に数値ラベル
        for bars in (bars_current, bars_projected):
            for bar in bars:
                height = bar.get_height()
                ax.text(
                    bar.get_x() + bar.get_width() / 2,
                    height + max(max(current), max(projected)) * 0.01,
                    f"{height:,.0f}",
                    ha="center",
                    va="bottom",
                    fontsize=8,
                )

        ax.set_xticks(x)
        ax.set_xticklabels(labels, fontsize=10)
        ax.set_title("補助事業の効果（現状 vs 予測）", fontsize=12, fontweight="bold")
        ax.yaxis.grid(True, linestyle="--", alpha=0.7, zorder=0)
        ax.set_axisbelow(True)
        ax.spines["top"].set_visible(False)
        ax.spines["right"].set_visible(False)
        ax.legend(fontsize=9)

        plt.tight_layout()

        out = Path(output_path).resolve()
        out.parent.mkdir(parents=True, exist_ok=True)
        fig.savefig(str(out), dpi=150, bbox_inches="tight")
        plt.close(fig)

        logger.info("効果比較グラフ保存完了: %s", out)
        return str(out)

    except Exception as exc:
        logger.error("効果比較グラフ生成エラー: %s", exc, exc_info=True)
        raise
