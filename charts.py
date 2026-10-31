"""集計レポートのグラフ（Altair）。画面から切り離しておくことで、グラフだけを確かめられるようにする。"""
import altair as alt
import pandas as pd

import devices
import reports

# 状態の5色は、配色の検証用プログラムで「色覚の違いがあっても隣どうしを見分けられる」ことを
# 確かめた並び。状態ごとに色を固定するので、0台の状態があっても他の状態の色は変わらない
STATUS_COLORS = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4"]
SERIES_COLOR = "#2a78d6"   # 1系列だけのグラフの色
SURFACE = "#FBFBF8"        # 画面の背景色（棒と棒の間のすき間にも使う）
TEXT = "#2F3B45"
MUTED = "#5F6B73"
GRID = "#E3E8EC"


def styled(chart):
    """見た目をそろえる：目盛りと補助線は控えめに、文字は本文と同じ系統の色にする。

    configure(...) は設定全体を作り直すので、必ず最初に呼ぶ（最後に呼ぶと、ほかの設定が消える）。
    """
    return (
        chart.configure(background=SURFACE)
        .configure_axis(labelColor=MUTED, titleColor=MUTED, gridColor=GRID, domainColor=GRID, tickColor=GRID,
                        labelFontSize=12, titleFontSize=12)
        .configure_legend(labelColor=TEXT, labelFontSize=12, orient="top", symbolType="square")
        .configure_view(strokeWidth=0)
    )


def bar_chart(df, category, value, tooltip, x_title, x_domain=None, x_ticks=None):
    """1系列の横棒グラフ（多い順に並べる）。マウスを乗せると値が出る。"""
    scale = alt.Scale(domain=x_domain) if x_domain else alt.Undefined
    axis = alt.Axis(values=x_ticks) if x_ticks else alt.Axis(tickMinStep=1)  # 台数は整数の目盛りだけにする
    chart = (
        alt.Chart(df)
        .mark_bar(color=SERIES_COLOR, cornerRadiusEnd=4, height=16)
        .encode(
            x=alt.X(f"{value}:Q", title=x_title, scale=scale, axis=axis),
            y=alt.Y(f"{category}:N", sort="-x", title=None),
            tooltip=tooltip,
        )
        .properties(height=max(len(df) * 30, 60), width="container")
    )
    return styled(chart)


def status_stack_chart(rows):
    """種別ごとの状態の内訳（積み上げの横棒）。rows は reports.devices_by_category_status() の結果。"""
    df = pd.DataFrame(rows)
    status_labels = [devices.STATUS_LABELS[s] for s in reports.STATUS_ORDER]
    df["順"] = df["状態"].map(status_labels.index)  # 積み上げの順番を、状態の並びどおりにする
    chart = (
        alt.Chart(df[df["台数"] > 0])
        # stroke に背景色を付けて、積み上げた色と色の間に 2px のすき間を作る
        .mark_bar(height=22, stroke=SURFACE, strokeWidth=2)
        .encode(
            x=alt.X("台数:Q", title="台数", axis=alt.Axis(tickMinStep=1)),
            y=alt.Y("種別:N", sort=list(devices.CATEGORY_LABELS.values()), title=None),
            color=alt.Color("状態:N", scale=alt.Scale(domain=status_labels, range=STATUS_COLORS), sort=status_labels,
                            legend=alt.Legend(title=None)),
            order=alt.Order("順:Q"),
            tooltip=["種別", "状態", "台数"],
        )
        .properties(height=140, width="container")
    )
    return styled(chart)


def department_chart(rows):
    """部署ごとの利用中の台数。"""
    return bar_chart(pd.DataFrame(rows), "部署", "台数", ["部署", "台数"], "台数")


def license_chart(rows):
    """ライセンスの利用率（0〜100%）。"""
    return bar_chart(pd.DataFrame(rows), "ライセンス名", "利用率（%）",
                     ["ライセンス名", "割当数", "保有数", "利用率（%）", "注意"],
                     "利用率（%）＝割当数÷保有数", x_domain=[0, 100], x_ticks=[0, 25, 50, 75, 100])
