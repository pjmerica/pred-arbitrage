// Exercise every tab / type filter / suspicious setting / sort (used by dash_smoke.js).
// Exercise every filter/tab combination in the pred-arb dashboard.
for (const tab of ['all','elections','sports','other']) {
  currentTab = tab;
  for (const t of ['ALL','guaranteed','unverified','pre-fee','price-gap','one-sided']) {
    fType = t;
    for (const s of ['hide','all','only']) { fSuspicious = s; render(); }
  }
}
for (const col of ['arb_type','raw_gap_pp','net_gap_pp','guaranteed_return_pct','settle_date','stake_a_dollars','depth_a_max_at_3pp','fuzzy_score','question_a','category']) {
  sortCol = col; for (const d of [1,-1]) { sortDir = d; render(); }
}
fType='ALL'; fSuspicious='all'; currentTab='all'; fMinVol=0; render();
