#!/usr/bin/env python3
"""
XRAY paper — figures + table for the M/G/1-with-vacations queueing analysis.
Reads /tmp/xray_queueing_model_results.json (produced by gen_queueing_model.py).
"""
import json
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

OUT = '/home/yeochan.yoon/banning/papers/xray/'
BLUE = '#2166AC'
ORANGE = '#D6604D'

plt.rcParams.update({
    'font.family': 'serif', 'font.size': 11, 'axes.labelsize': 12,
    'legend.fontsize': 10, 'xtick.labelsize': 10, 'ytick.labelsize': 10,
    'svg.fonttype': 'none',
})

d = json.load(open('/tmp/xray_queueing_model_results.json'))

# ── Figure: duration axis (the clean natural experiment: tps/slots fixed) ──
dur_labels = ['anchor_lat', 'dur300', 'dur600']
dur_x = [120, 300, 600]

fig, axes = plt.subplots(1, 2, figsize=(10.5, 4.0))
ax0, ax1 = axes

for platform, color, marker in [('besu', BLUE, 'o'), ('nm', ORANGE, 's')]:
    r_vals = [d[l][platform]['residual'] for l in dur_labels]
    p99_vals = [d[l][platform]['p99'] for l in dur_labels]
    label = 'Besu (G1GC)' if platform == 'besu' else 'Nethermind (CLR)'
    ax0.plot(dur_x, r_vals, marker=marker, color=color, lw=1.8, ms=7, label=label)
    ax1.plot(dur_x, p99_vals, marker=marker, color=color, lw=1.8, ms=7, label=label)

ax0.set_xlabel('Stress-window duration (s)\n(a) Residual-vacation statistic $R$', fontsize=9.5)
ax0.set_ylabel('Residual-vacation statistic $R$ (ms)')
ax0.grid(alpha=0.3)
ax0.legend(loc='best')

ax1.set_xlabel('Stress-window duration (s)\n(b) Observed p99 tx latency', fontsize=9.5)
ax1.set_ylabel('Observed p99 tx latency (ms)')
ax1.grid(alpha=0.3)

fig.tight_layout()
for ext in ('pdf', 'svg', 'png'):
    fig.savefig(f'{OUT}xray_fig10_queueing_duration.{ext}', format=ext, bbox_inches='tight', pad_inches=0.3, dpi=200)
plt.close(fig)
print('saved xray_fig10_queueing_duration')
