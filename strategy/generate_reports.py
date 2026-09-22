"""대화형 백테스트 대시보드 및 리포트 생성기 (T9)

data/t9_backtest_results.json, data/t9_equity_curves.csv, data/t9_top_bottom_calls.json을 읽어
브라우저에서 바로 확인할 수 있는 단독 실행형 인터랙티브 HTML 대시보드(review/backtest_dashboard.html)를 생성합니다.
"""

import json
from pathlib import Path
import pandas as pd

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
REVIEW_DIR = BASE_DIR / "review"
EQUITY_CSV = DATA_DIR / "t9_equity_curves.csv"
RESULTS_JSON = DATA_DIR / "t9_backtest_results.json"
CALLS_JSON = DATA_DIR / "t9_top_bottom_calls.json"


def generate_html_dashboard():
    REVIEW_DIR.mkdir(exist_ok=True)
    
    with open(RESULTS_JSON, "r", encoding="utf-8") as f:
        results = json.load(f)
    with open(CALLS_JSON, "r", encoding="utf-8") as f:
        calls = json.load(f)
        
    equity_df = pd.read_csv(EQUITY_CSV)
    
    dates = equity_df["date"].tolist()
    
    # 전략별 시계열 데이터
    series_map = {
        "GK_Alpha (Net 10bp)": {"data": equity_df["cum_gk_net"].tolist(), "color": "#10b981", "width": 3.5},
        "GK_Alpha (Gross)": {"data": equity_df["cum_gk_gross"].tolist(), "color": "#059669", "width": 2.0, "dash": "dot"},
        "Regime_Gated (Net)": {"data": equity_df["cum_regime_net"].tolist(), "color": "#6366f1", "width": 2.5},
        "Base_L4 (슈카 본안)": {"data": equity_df["cum_base_L4"].tolist(), "color": "#3b82f6", "width": 2.5},
        "LongOnly_L4 (롱온리)": {"data": equity_df["cum_long_only_L4"].tolist(), "color": "#06b6d4", "width": 2.0},
        "Inverse_L4 (인버스 본안)": {"data": equity_df["cum_inv_L4"].tolist(), "color": "#ef4444", "width": 2.0, "dash": "dash"},
        "P24_EW (동일가중 벤치마크)": {"data": equity_df["cum_P24_EW"].tolist(), "color": "#f59e0b", "width": 2.5},
        "DJGT (글로벌 타이탄스 50)": {"data": equity_df["cum_DJGT"].tolist(), "color": "#8b5cf6", "width": 2.5},
        "SOX (필라델피아 반도체)": {"data": equity_df["cum_SOX"].tolist(), "color": "#9ca3af", "width": 1.5, "dash": "dot"},
    }
    
    metrics = results["strategy_metrics"]
    
    # HTML 템플릿 생성
    html_content = f"""<!DOCTYPE html>
<html lang="ko">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>슈카 ETF (P24) 통합 백테스트 대시보드 (2021~2026)</title>
    <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
    <style>
        :root {{
            --bg: #0f172a;
            --card-bg: #1e293b;
            --text: #f8fafc;
            --text-dim: #94a3b8;
            --border: #334155;
            --accent: #10b981;
            --danger: #ef4444;
            --primary: #3b82f6;
        }}
        body {{
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Noto Sans KR', sans-serif;
            background-color: var(--bg);
            color: var(--text);
            margin: 0;
            padding: 24px;
        }}
        .container {{
            max-width: 1300px;
            margin: 0 auto;
        }}
        header {{
            margin-bottom: 28px;
            border-bottom: 1px solid var(--border);
            padding-bottom: 20px;
        }}
        h1 {{
            font-size: 28px;
            margin: 0 0 8px 0;
            color: #fff;
        }}
        .subtitle {{
            color: var(--text-dim);
            font-size: 15px;
        }}
        .grid-cards {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
            gap: 16px;
            margin-bottom: 28px;
        }}
        .card {{
            background: var(--card-bg);
            border: 1px solid var(--border);
            border-radius: 12px;
            padding: 20px;
        }}
        .card-label {{
            font-size: 13px;
            color: var(--text-dim);
            text-transform: uppercase;
            letter-spacing: 0.5px;
            margin-bottom: 6px;
        }}
        .card-val {{
            font-size: 26px;
            font-weight: 700;
        }}
        .pos {{ color: #10b981; }}
        .neg {{ color: #ef4444; }}
        .chart-box {{
            background: var(--card-bg);
            border: 1px solid var(--border);
            border-radius: 12px;
            padding: 24px;
            margin-bottom: 28px;
        }}
        .chart-title {{
            font-size: 18px;
            font-weight: 600;
            margin-bottom: 16px;
            display: flex;
            justify-content: space-between;
            align-items: center;
        }}
        table {{
            width: 100%;
            border-collapse: collapse;
            font-size: 14px;
            text-align: right;
        }}
        th, td {{
            padding: 12px 14px;
            border-bottom: 1px solid var(--border);
        }}
        th {{
            background: #111827;
            color: var(--text-dim);
            font-weight: 600;
        }}
        th:first-child, td:first-child {{
            text-align: left;
        }}
        tr:hover {{
            background: #273549;
        }}
        .calls-grid {{
            display: grid;
            grid-template-columns: 1fr 1fr;
            gap: 20px;
            margin-top: 28px;
        }}
        .call-item {{
            background: #111827;
            border-left: 4px solid var(--accent);
            border-radius: 6px;
            padding: 14px 16px;
            margin-bottom: 12px;
        }}
        .call-item.bad {{
            border-left-color: var(--danger);
        }}
        .call-meta {{
            font-size: 12px;
            color: var(--text-dim);
            margin-bottom: 4px;
            display: flex;
            justify-content: space-between;
        }}
        .call-quote {{
            font-style: italic;
            font-size: 13px;
            color: #cbd5e1;
            margin-top: 6px;
            line-height: 1.4;
        }}
    </style>
</head>
<body>
    <div class="container">
        <header>
            <h1>슈카 ETF (P24) 통합 백테스트 결과 대시보드</h1>
            <div class="subtitle">
                "슈카 말만 듣고 투자했다면 2021~2026" | 표본 기간: 2021-04-30 ~ 2026-09-11 (280주, 5년 4개월) | 18,308건 자막 분석
            </div>
        </header>

        <div class="grid-cards">
            <div class="card">
                <div class="card-label">GK 퀀트 앙상블 (Net)</div>
                <div class="card-val pos">+{metrics['ret_gk_net']['cumulative_return']*100:.1f}%</div>
                <div style="font-size: 12px; color: var(--text-dim); margin-top: 4px;">샤프 {metrics['ret_gk_net']['sharpe_ratio']:.2f} | MDD {metrics['ret_gk_net']['max_drawdown']*100:.1f}%</div>
            </div>
            <div class="card">
                <div class="card-label">슈카 정방향 본안 (Base L4)</div>
                <div class="card-val pos">+{metrics['ret_base_L4']['cumulative_return']*100:.1f}%</div>
                <div style="font-size: 12px; color: var(--text-dim); margin-top: 4px;">샤프 {metrics['ret_base_L4']['sharpe_ratio']:.2f} | MDD {metrics['ret_base_L4']['max_drawdown']*100:.1f}%</div>
            </div>
            <div class="card">
                <div class="card-label">슈카 인버스 본안 (Inv L4)</div>
                <div class="card-val neg">{metrics['ret_inv_L4']['cumulative_return']*100:.1f}%</div>
                <div style="font-size: 12px; color: var(--text-dim); margin-top: 4px;">샤프 {metrics['ret_inv_L4']['sharpe_ratio']:.2f} | MDD {metrics['ret_inv_L4']['max_drawdown']*100:.1f}%</div>
            </div>
            <div class="card">
                <div class="card-label">벤치마크 (DJGT 50)</div>
                <div class="card-val pos">+{metrics['ret_DJGT']['cumulative_return']*100:.1f}%</div>
                <div style="font-size: 12px; color: var(--text-dim); margin-top: 4px;">샤프 {metrics['ret_DJGT']['sharpe_ratio']:.2f} | MDD {metrics['ret_DJGT']['max_drawdown']*100:.1f}%</div>
            </div>
        </div>

        <div class="chart-box">
            <div class="chart-title">
                <span>누적 자산 가치 곡선 (Equity Curves, 2021-04-30 = 1.0)</span>
            </div>
            <canvas id="equityChart" height="90"></canvas>
        </div>

        <div class="chart-box">
            <div class="chart-title">전략 및 벤치마크 종합 성과 비교표</div>
            <table>
                <thead>
                    <tr>
                        <th>전략 및 벤치마크</th>
                        <th>누적 수익률</th>
                        <th>연평균(CAGR)</th>
                        <th>연변동성</th>
                        <th>샤프비율</th>
                        <th>최대낙폭(MDD)</th>
                        <th>칼마비율</th>
                        <th>주간 승률</th>
                        <th>정보비율(IR)</th>
                    </tr>
                </thead>
                <tbody>"""

    for strat_key, strat_name in [
        ("ret_gk_net", "★ Grinold-Kahn 퀀트 앙상블 (Net 10bp)"),
        ("ret_gk_gross", "Grinold-Kahn 연속 알파 (Gross)"),
        ("ret_regime_net", "HMM 국면 게이팅 (Net 10bp)"),
        ("ret_long_only_L4", "슈카 롱 온리 (Long-Only L4)"),
        ("ret_base_L4", "슈카 정방향 본안 (Base L4 롱숏)"),
        ("ret_base_L1", "슈카 1주 민감도 (Sensitivity L1)"),
        ("ret_delta_D4", "슈카 4주 변화량 (Delta D4)"),
        ("ret_inv_L4", "슈카 인버스 본안 (Inverse L4)"),
        ("ret_P24_EW", "벤치마크: P24 동일가중 매수보유"),
        ("ret_DJGT", "벤치마크: DJ Global Titans 50"),
        ("ret_SOX", "참고: 필라델피아 반도체 지수"),
    ]:
        m = metrics[strat_key]
        cum_class = "pos" if m["cumulative_return"] > 0 else "neg"
        html_content += f"""
                    <tr>
                        <td><strong>{strat_name}</strong></td>
                        <td class="{cum_class}"><strong>{m['cumulative_return']*100:+.2f}%</strong></td>
                        <td>{m['cagr']*100:+.2f}%</td>
                        <td>{m['annualized_volatility']*100:.2f}%</td>
                        <td><strong>{m['sharpe_ratio']:+.2f}</strong></td>
                        <td class="neg">{m['max_drawdown']*100:.2f}%</td>
                        <td>{m['calmar_ratio']:.2f}</td>
                        <td>{m['win_rate']*100:.1f}%</td>
                        <td>{m['information_ratio']:+.2f}</td>
                    </tr>"""

    html_content += """
                </tbody>
            </table>
        </div>

        <div class="calls-grid">
            <div class="card">
                <div class="chart-title" style="color: #10b981;">🏆 Top 5 최고 적중 발언 (대박 픽)</div>"""

    for item in calls["top_5_best_calls"]:
        html_content += f"""
                <div class="call-item">
                    <div class="call-meta">
                        <span><strong>{item['entry_date']} ({item['asset_id']})</strong></span>
                        <span class="pos">주간 +{item['weekly_return_pct']:.1f}% (PnL +{item['pnl_contribution_pct']:.2f}%)</span>
                    </div>
                    <div><a href="{item['video_url']}" target="_blank" style="color: #60a5fa; text-decoration: none;">▶ {item['video_title']}</a></div>
                    <div class="call-quote">"{item['quote']}"</div>
                </div>"""

    html_content += """
            </div>
            <div class="card">
                <div class="chart-title" style="color: #ef4444;">💀 Top 5 최악 오판 발언 (쪽박 픽)</div>"""

    for item in calls["top_5_worst_calls"]:
        html_content += f"""
                <div class="call-item bad">
                    <div class="call-meta">
                        <span><strong>{item['entry_date']} ({item['asset_id']})</strong></span>
                        <span class="neg">주간 {item['weekly_return_pct']:.1f}% (PnL {item['pnl_contribution_pct']:.2f}%)</span>
                    </div>
                    <div><a href="{item['video_url']}" target="_blank" style="color: #f87171; text-decoration: none;">▶ {item['video_title']}</a></div>
                    <div class="call-quote">"{item['quote']}"</div>
                </div>"""

    html_content += f"""
            </div>
        </div>
    </div>

    <script>
        const ctx = document.getElementById('equityChart').getContext('2d');
        const labels = {json.dumps(dates)};
        
        const datasets = [
            {{
                label: 'GK_Alpha (Net 10bp)',
                data: {json.dumps(series_map['GK_Alpha (Net 10bp)']['data'])},
                borderColor: '#10b981',
                borderWidth: 3,
                tension: 0.1,
                pointRadius: 0
            }},
            {{
                label: 'Regime_Gated (Net)',
                data: {json.dumps(series_map['Regime_Gated (Net)']['data'])},
                borderColor: '#6366f1',
                borderWidth: 2,
                tension: 0.1,
                pointRadius: 0
            }},
            {{
                label: 'Base_L4 (슈카 본안 롱숏)',
                data: {json.dumps(series_map['Base_L4 (슈카 본안)']['data'])},
                borderColor: '#3b82f6',
                borderWidth: 2,
                tension: 0.1,
                pointRadius: 0
            }},
            {{
                label: 'LongOnly_L4 (롱온리)',
                data: {json.dumps(series_map['LongOnly_L4 (롱온리)']['data'])},
                borderColor: '#06b6d4',
                borderWidth: 1.5,
                tension: 0.1,
                pointRadius: 0
            }},
            {{
                label: 'Inverse_L4 (인버스)',
                data: {json.dumps(series_map['Inverse_L4 (인버스 본안)']['data'])},
                borderColor: '#ef4444',
                borderWidth: 1.5,
                borderDash: [5, 5],
                tension: 0.1,
                pointRadius: 0
            }},
            {{
                label: 'P24_EW (동일가중 벤치마크)',
                data: {json.dumps(series_map['P24_EW (동일가중 벤치마크)']['data'])},
                borderColor: '#f59e0b',
                borderWidth: 2,
                tension: 0.1,
                pointRadius: 0
            }},
            {{
                label: 'DJGT (글로벌 타이탄스 50)',
                data: {json.dumps(series_map['DJGT (글로벌 타이탄스 50)']['data'])},
                borderColor: '#8b5cf6',
                borderWidth: 2,
                tension: 0.1,
                pointRadius: 0
            }}
        ];

        new Chart(ctx, {{
            type: 'line',
            data: {{ labels: labels, datasets: datasets }},
            options: {{
                responsive: true,
                interaction: {{ mode: 'index', intersect: false }},
                scales: {{
                    x: {{ grid: {{ color: '#1e293b' }}, ticks: {{ color: '#64748b', maxTicksLimit: 12 }} }},
                    y: {{ grid: {{ color: '#1e293b' }}, ticks: {{ color: '#64748b' }} }}
                }},
                plugins: {{
                    legend: {{ labels: {{ color: '#cbd5e1', boxWidth: 12 }} }}
                }}
            }}
        }});
    </script>
</body>
</html>
"""
    dash_path = REVIEW_DIR / "backtest_dashboard.html"
    dash_path.write_text(html_content, encoding="utf-8")
    print(f"인터랙티브 대시보드 저장 완료: {dash_path}")


if __name__ == "__main__":
    generate_html_dashboard()
