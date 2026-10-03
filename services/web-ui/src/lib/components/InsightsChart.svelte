<script lang="ts">
  /**
   * Thin Chart.js wrapper for the Insights section (B2).
   *
   * Chart.js was picked over layerchart: it's a single small, framework-agnostic
   * package (no D3 dependency tree) that renders straight to a <canvas>, which
   * keeps the Svelte side to "create on mount, update on data change, destroy
   * on unmount" — no snippet/slot API to learn for line/bar charts.
   */
  import { onDestroy } from 'svelte';
  import {
    Chart,
    LineController,
    BarController,
    LineElement,
    BarElement,
    PointElement,
    LinearScale,
    CategoryScale,
    TimeScale,
    Tooltip,
    Legend,
    Filler,
    type ChartConfiguration,
  } from 'chart.js';

  Chart.register(
    LineController, BarController, LineElement, BarElement, PointElement,
    LinearScale, CategoryScale, TimeScale, Tooltip, Legend, Filler,
  );

  let {
    type = 'line',
    labels,
    datasets,
    height = 180,
    yTicksSuffix = '',
  }: {
    type?: 'line' | 'bar';
    labels: string[];
    datasets: { label: string; data: number[]; color: string }[];
    height?: number;
    yTicksSuffix?: string;
  } = $props();

  let canvasEl: HTMLCanvasElement | undefined = $state();
  let chart: Chart | null = null;

  function buildConfig(): ChartConfiguration {
    return {
      type,
      data: {
        labels,
        datasets: datasets.map((d) => ({
          label: d.label,
          data: d.data,
          borderColor: d.color,
          backgroundColor: type === 'bar' ? d.color : `${d.color}33`,
          fill: type === 'line',
          tension: 0.3,
          pointRadius: 0,
          borderWidth: 2,
        })),
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        interaction: { mode: 'index', intersect: false },
        plugins: {
          legend: { display: datasets.length > 1, labels: { color: '#94a3b8', boxWidth: 10, font: { size: 11 } } },
          tooltip: { mode: 'index', intersect: false },
        },
        scales: {
          x: { ticks: { color: '#64748b', font: { size: 10 }, maxRotation: 0 }, grid: { display: false } },
          y: {
            ticks: {
              color: '#64748b',
              font: { size: 10 },
              callback: (v) => `${v}${yTicksSuffix}`,
            },
            grid: { color: 'rgba(148,163,184,0.1)' },
            beginAtZero: true,
          },
        },
      },
    };
  }

  $effect(() => {
    if (!canvasEl) return;
    // Re-read labels/datasets so this effect reruns on data change.
    const cfg = buildConfig();
    if (chart) {
      chart.data = cfg.data;
      chart.update();
    } else {
      chart = new Chart(canvasEl, cfg);
    }
  });

  onDestroy(() => {
    chart?.destroy();
    chart = null;
  });
</script>

<div style="height: {height}px">
  <canvas bind:this={canvasEl}></canvas>
</div>
