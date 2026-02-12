(function () {
  const cfg = window.dashboardConfig || {};
  const subjectEl = document.getElementById('chartSubject');
  const groupEl = document.getElementById('chartGroup');
  const ppGapEl = document.getElementById('ppGapHeadline');
  const bandsEl = document.getElementById('bandsChart');
  const onTrackEl = document.getElementById('onTrackChart');
  const ppEl = document.getElementById('ppCompareChart');
  if (!cfg.apiUrl || !bandsEl || !onTrackEl || !ppEl) return;

  let bandsChart, onTrackChart, ppChart;

  function drawOrUpdate(chart, el, type, data, options) {
    if (chart) {
      chart.data = data;
      chart.options = options || chart.options;
      chart.update();
      return chart;
    }
    return new Chart(el, { type, data, options });
  }

  function load() {
    const params = new URLSearchParams({
      year: cfg.year || '',
      term: cfg.term || 'Autumn',
      subject: (subjectEl && subjectEl.value) || 'maths',
      group: (groupEl && groupEl.value) || 'all'
    });
    if (cfg.classId && cfg.classId !== 'all') params.set('class', cfg.classId);
    fetch(cfg.apiUrl + '?' + params.toString())
      .then(r => r.json())
      .then(data => {
        bandsChart = drawOrUpdate(bandsChart, bandsEl, 'bar', {
          labels: ['WT', 'WA', 'WA+'],
          datasets: [{ label: 'Pupils', data: [data.bands.WT, data.bands.WA, data.bands.WAplus], backgroundColor: ['#ef4444', '#22c55e', '#f59e0b'] }]
        }, { responsive: true, plugins: { legend: { display: false } } });

        onTrackChart = drawOrUpdate(onTrackChart, onTrackEl, 'bar', {
          labels: ['On track', 'Not on track'],
          datasets: [{ label: 'Count', data: [data.on_track.yes, data.on_track.no], backgroundColor: ['#16a34a', '#dc2626'] }]
        }, { responsive: true, plugins: { legend: { display: false } } });

        ppChart = drawOrUpdate(ppChart, ppEl, 'bar', {
          labels: ['PP WT', 'PP WA', 'PP WA+', 'Non-PP WT', 'Non-PP WA', 'Non-PP WA+'],
          datasets: [{
            label: 'Attainment split',
            data: [
              data.pp_compare.pp.WT, data.pp_compare.pp.WA, data.pp_compare.pp.WAplus,
              data.pp_compare.non_pp.WT, data.pp_compare.non_pp.WA, data.pp_compare.non_pp.WAplus
            ],
            backgroundColor: ['#fca5a5', '#86efac', '#fcd34d', '#f87171', '#4ade80', '#fbbf24']
          }]
        }, { responsive: true, plugins: { legend: { display: false } } });

        if (ppGapEl && data.pp_gap) {
          ppGapEl.textContent = `PP ${data.pp_gap.pp}% | Non-PP ${data.pp_gap.non_pp}% | Gap ${data.pp_gap.gap}pp`;
        }
      });
  }

  if (subjectEl) subjectEl.addEventListener('change', load);
  if (groupEl) groupEl.addEventListener('change', load);
  load();
})();
