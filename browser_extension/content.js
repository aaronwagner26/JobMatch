const DETAIL_CAPTURE_LIMIT = 24;
const DETAIL_CAPTURE_TIMEOUT_MS = 1600;
const DETAIL_CAPTURE_POLL_MS = 140;
const MAX_CONSECUTIVE_DETAIL_SKIPS = 3;

chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
  if (message?.type !== 'jobmatch_capture_page') {
    return undefined;
  }

  void (async () => {
    try {
      sendResponse({ ok: true, payload: await captureCurrentPage() });
    } catch (error) {
      sendResponse({
        ok: false,
        error: error instanceof Error ? error.message : String(error),
      });
    }
  })();
  return true;
});

async function captureCurrentPage() {
  const host = location.hostname.toLowerCase();
  if (host.endsWith('linkedin.com') && location.pathname.toLowerCase().includes('/company/') && location.pathname.toLowerCase().includes('/jobs')) {
    return captureLinkedInCompanyPage();
  }
  if (host.includes('indeed.')) {
    return captureIndeedPage();
  }
  return captureGenericPage();
}

async function captureLinkedInCompanyPage() {
  const company = firstText(document, [
    '.org-top-card-summary__title',
    '.job-details-jobs-unified-top-card__company-name',
    '.jobs-company__name',
    'h1',
  ]);
  const cards = uniqueNodes(
    Array.from(document.querySelectorAll('[data-job-id], li.jobs-search-results__list-item, li.scaffold-layout__list-item'))
      .filter((card) => isVisible(card))
  );
  const detailMap = await captureVisibleResultDetails(cards, {
    getRawId: (card) => normalizeText(card.getAttribute('data-job-id') || firstNode(card, [
      'a[href*="/jobs/view/"]',
      'a.job-card-list__title',
      'a.job-card-container__link',
    ])?.getAttribute('data-job-id') || ''),
    getClickTarget: (card) => firstNode(card, [
      'a[href*="/jobs/view/"]',
      'a.job-card-list__title',
      'a.job-card-container__link',
      '[data-job-id]',
    ]) || card,
    captureDetail: (rawId, options = {}) => captureLinkedInSelectedDetail(rawId, options),
  });
  const detail = captureLinkedInSelectedDetail();
  const jobs = uniqueJobs(cards.map((card) => {
    const anchor = firstNode(card, [
      'a[href*="/jobs/view/"]',
      'a.job-card-list__title',
      'a.job-card-container__link',
    ]);
    const url = absoluteUrl(anchor?.href || '');
    const rawId = card.getAttribute('data-job-id') || anchor?.getAttribute('data-job-id') || jobIdFromUrl(url);
    const selected = detailMap.get(rawId) || (detail && rawId && detail.raw_id === rawId ? detail : null);
    return {
      raw_id: rawId || url,
      title: firstText(card, ['.job-card-list__title', '.job-card-container__link', 'strong']) || selected?.title || text(anchor),
      company: company || selected?.company || firstText(card, ['.artdeco-entity-lockup__subtitle', '.job-card-container__company-name']),
      location: firstText(card, ['.job-card-container__metadata-item', '.artdeco-entity-lockup__caption']) || selected?.location || '',
      summary: selected?.summary || firstText(card, ['.job-card-container__footer-job-state', '.job-card-list__footer-wrapper']) || '',
      description: selected?.description || '',
      url: selected?.url || url,
    };
  }));
  return buildCapturePayload('linkedin_company', 'LinkedIn', company, jobs);
}

function captureLinkedInSelectedDetail(rawIdHint = '', options = {}) {
  const useHint = Boolean(options.useHint);
  const url = absoluteUrl(
    firstNode(document, [
      'a[href*="/jobs/view/"].job-details-jobs-unified-top-card__job-title-link',
      '.jobs-unified-top-card__content a[href*="/jobs/view/"]',
      'a[href*="/jobs/view/"]',
    ])?.href || location.href
  );
  const description = firstText(document, [
    '.jobs-description-content__text',
    '.jobs-box__html-content',
    '.jobs-description__content',
  ]);
  const rawId = jobIdFromUrl(url) || jobIdFromUrl(location.href) || (useHint ? normalizeText(rawIdHint) : '');
  if (!rawId) {
    return null;
  }
  return {
    raw_id: rawId,
    title: firstText(document, [
      '.job-details-jobs-unified-top-card__job-title',
      '.jobs-unified-top-card__job-title',
      'h1',
    ]),
    company: firstText(document, [
      '.job-details-jobs-unified-top-card__company-name',
      '.jobs-unified-top-card__company-name',
    ]),
    location: firstText(document, [
      '.job-details-jobs-unified-top-card__primary-description-container',
      '.jobs-unified-top-card__primary-description',
    ]),
    summary: excerpt(description, 280),
    description,
    url,
  };
}

async function captureIndeedPage() {
  const cards = uniqueNodes(
    Array.from(document.querySelectorAll('[data-jk], [data-testid="slider_item"], a[href*="/viewjob"]'))
      .map((node) => node.tagName === 'A' ? node.closest('[data-jk], [data-testid="slider_item"], article, li, div') || node : node)
      .filter(Boolean)
      .filter((node) => isVisible(node))
  );
  const detailMap = await captureVisibleResultDetails(cards, {
    getRawId: (node) => {
      const anchor = node.tagName === 'A' ? node : firstNode(node, ['a[href*="/viewjob"]', 'a[href*="jk="]', 'a[href*="/rc/clk"]', 'a[href*="/pagead/clk"]']);
      return normalizeText(node?.getAttribute?.('data-jk') || anchor?.getAttribute?.('data-jk') || jobIdFromUrl(anchor?.href || ''));
    },
    getClickTarget: (node) => firstNode(node, ['a[href*="/viewjob"]', '[data-jk]']) || node,
    captureDetail: (rawId, options = {}) => captureIndeedSelectedDetail(rawId, options),
    shouldSkipDetailWalk: (node) => looksLikeGroupedOpeningsCard(node),
  });
  const detail = captureIndeedSelectedDetail();
  if (cards.length === 0 && detail) {
    return buildCapturePayload('indeed_job', 'Indeed', detail.company || detail.title, [detail]);
  }
  const jobs = uniqueJobs(cards.map((node) => {
    const anchor = node.tagName === 'A' ? node : firstNode(node, ['a[href*="/viewjob"]', 'a[href*="jk="]', 'a[href*="/rc/clk"]', 'a[href*="/pagead/clk"]']);
    const container = node.tagName === 'A' ? node.closest('[data-jk], [data-testid="slider_item"], article, li, div') || node : node;
    const rawId = normalizeText(container?.getAttribute?.('data-jk') || anchor?.getAttribute?.('data-jk') || jobIdFromUrl(anchor?.href || ''));
    const url = canonicalIndeedJobUrl(anchor?.href || '', rawId);
    const selected = detailMap.get(rawId) || (detail && rawId && detail.raw_id === rawId ? detail : null);
    return {
      raw_id: rawId || url,
      title: text(anchor),
      company: firstText(container, ['.companyName', '[data-testid="company-name"]']),
      location: firstText(container, ['.companyLocation', '[data-testid="text-location"]']),
      summary: selected?.summary || firstText(container, ['.job-snippet', '[data-testid="job-snippet"]']),
      description: selected?.description || '',
      salary_text: selected?.salary_text || firstSalaryText(texts(container, ['.salary-snippet', '.estimated-salary', '[class*="salary"]', '[data-testid="attribute_snippet_testid"]'])),
      employment_text: selected?.employment_text || '',
      url: selected?.url || url,
      posted_at: firstText(container, ['.date', 'time']),
    };
  }));
  if (jobs.length === 0 && detail) {
    jobs.push(detail);
  }
  const queryLabel = firstText(document, ['h1', '[data-testid="jobsearch-HeroLabel"]']) || document.title;
  return buildCapturePayload('indeed_search', 'Indeed', queryLabel, jobs);
}

function captureIndeedSelectedDetail(rawIdHint = '', options = {}) {
  const useHint = Boolean(options.useHint);
  const jsonLdJobs = extractJsonLdJobs();
  const locationUrl = absoluteUrl(location.href);
  const rawId = normalizeText(
    jobIdFromUrl(locationUrl)
    || firstNode(document, ['[data-jk][aria-current="true"]', '[data-jk].resultContent-active', '[data-jk].job_seen_beacon'])?.getAttribute('data-jk')
    || (useHint ? rawIdHint : '')
    || ''
  );
  const title = firstText(document, [
    'h1',
    '[data-testid="jobsearch-JobInfoHeader-title"]',
    '[data-testid="viewJobTitle"]',
  ]);
  const jsonLdMatch = jsonLdJobs.find((job) => job.raw_id === rawId || (title && job.title === title)) || null;
  const description = firstText(document, [
    '#jobDescriptionText',
    '.jobsearch-JobComponent-description',
    '.jobsearch-jobDescriptionText',
    '.jobDescriptionText',
  ]) || jsonLdMatch?.description || '';
  const salaryCandidates = [
    ...texts(document, [
      '.salaryText',
      '#salaryInfoAndJobType',
      '[data-testid="salaryInfoAndJobType"]',
      '.jobsearch-OtherJobDetailsContainer',
      '[data-testid="jobsearch-CollapsedEmbeddedHeader-salary"]',
      '#jobDetailsSection [aria-label="Pay"]',
      '[data-testid="attribute_snippet_testid"]',
      '[class*="salary"]',
    ]),
    jsonLdMatch?.salary_text || '',
  ].filter(Boolean);
  const salaryText = firstSalaryText(salaryCandidates);
  const employmentText = texts(document, [
    '[data-testid="salaryInfoAndJobType"]',
    '[data-testid="attribute_snippet_testid"]',
  ]).filter((value) => value && value !== salaryText).join(' | ');
  const company = firstText(document, [
    '.companyName',
    '[data-testid="inlineHeader-companyName"]',
    '[data-company-name="true"]',
    '.jobsearch-CompanyInfoContainer a',
  ]) || jsonLdMatch?.company || '';
  const detailTitle = title || jsonLdMatch?.title || '';
  if (!detailTitle && !rawId) {
    return null;
  }
  return {
    raw_id: rawId || jsonLdMatch?.raw_id || locationUrl,
    title: detailTitle,
    company,
    location: firstText(document, [
      '.companyLocation',
      '[data-testid="inlineHeader-companyLocation"]',
      '[data-testid="job-location"]',
    ]) || jsonLdMatch?.location || '',
    summary: jsonLdMatch?.summary || excerpt(description, 280),
    description,
    salary_text: salaryText,
    employment_text: employmentText,
    url: canonicalIndeedJobUrl(jsonLdMatch?.url || locationUrl, rawId || jsonLdMatch?.raw_id || ''),
    posted_at: firstText(document, ['.date', '[data-testid="myJobsStateDate"]', 'time']),
  };
}

function captureGenericPage() {
  let jobs = extractJsonLdJobs();
  if (jobs.length === 0) {
    jobs = uniqueJobs(
      Array.from(document.querySelectorAll('article, li, .job, .posting, .job-listing, .result')).map((container) => {
        const anchor = firstNode(container, ['a[href]']);
        const href = anchor?.getAttribute('href') || '';
        const url = absoluteUrl(href);
        const title = text(anchor);
        if (!title || !url || !looksLikeJobUrl(url)) {
          return null;
        }
        return {
          raw_id: url,
          title,
          company: firstText(container, ['.company', '.posting-company', '[itemprop="hiringOrganization"]']),
          location: firstText(container, ['.location', '[itemprop="jobLocation"]']),
          summary: firstText(container, ['.description', '.summary', 'p']),
          description: '',
          salary_text: firstSalaryText(texts(container, ['.salary', '.salary-range', '.compensation', '[class*="salary"]'])),
          url,
        };
      })
    );
  }
  const label = firstText(document, ['h1']) || document.title || location.hostname;
  return buildCapturePayload('generic_page', siteNameFromHost(location.hostname), label, jobs);
}

function buildCapturePayload(parser, site, companyOrLabel, jobs) {
  return {
    parser,
    page: {
      url: location.href,
      title: document.title,
      site,
      parser,
      captured_at: new Date().toISOString(),
    },
    source: {
      url: location.href,
      site,
      company: normalizeText(companyOrLabel),
      name: sourceNameFor(site, companyOrLabel),
    },
    jobs,
  };
}

function sourceNameFor(site, companyOrLabel) {
  const label = normalizeText(companyOrLabel) || site || 'Captured Jobs';
  return `Capture: ${label}${site && !label.includes(site) ? ` (${site})` : ''}`;
}

function extractJsonLdJobs() {
  const jobs = [];
  for (const script of document.querySelectorAll('script[type="application/ld+json"]')) {
    const rawText = script.textContent || '';
    if (!rawText.trim()) {
      continue;
    }
    try {
      const payload = JSON.parse(rawText);
      collectJsonLdJob(payload, jobs);
    } catch (_error) {
      continue;
    }
  }
  return uniqueJobs(jobs);
}

function collectJsonLdJob(payload, jobs) {
  if (!payload) {
    return;
  }
  if (Array.isArray(payload)) {
    payload.forEach((item) => collectJsonLdJob(item, jobs));
    return;
  }
  if (typeof payload !== 'object') {
    return;
  }
  const recordType = String(payload['@type'] || '').toLowerCase();
  if (recordType === 'jobposting') {
    const organization = payload.hiringOrganization || {};
    jobs.push({
      raw_id: payload.identifier?.value || payload.url || payload.title,
      title: normalizeText(payload.title),
      company: normalizeText(organization.name || ''),
      location: flattenJsonLdLocation(payload.jobLocation),
      summary: normalizeText(payload.description || ''),
      description: normalizeText(payload.description || ''),
      employment_text: normalizeText(payload.employmentType || ''),
      salary_text: jsonLdSalaryText(payload),
      posted_at: payload.datePosted || '',
      url: absoluteUrl(payload.url || ''),
    });
  }
  if (payload['@graph']) {
    collectJsonLdJob(payload['@graph'], jobs);
  }
}

function flattenJsonLdLocation(location) {
  if (!location) {
    return '';
  }
  if (Array.isArray(location)) {
    return normalizeText(location.map((item) => flattenJsonLdLocation(item)).filter(Boolean).join(', '));
  }
  if (typeof location === 'object') {
    const address = location.address || {};
    return normalizeText([
      address.addressLocality,
      address.addressRegion,
      address.addressCountry,
      location.name,
    ].filter(Boolean).join(', '));
  }
  return normalizeText(String(location));
}

function uniqueJobs(jobs) {
  const seen = new Set();
  const output = [];
  for (const job of jobs) {
    if (!job || !job.title) {
      continue;
    }
    const key = String(job.raw_id || job.url || `${job.title}|${job.company || ''}`);
    if (seen.has(key)) {
      continue;
    }
    seen.add(key);
    output.push(job);
  }
  return output;
}

async function captureVisibleResultDetails(cards, options) {
  const detailMap = new Map();
  let consecutiveSkips = 0;
  for (const card of cards.slice(0, DETAIL_CAPTURE_LIMIT)) {
    const rawId = normalizeText(options.getRawId(card) || '');
    if (!rawId || detailMap.has(rawId)) {
        continue;
    }
    if (typeof options.shouldSkipDetailWalk === 'function' && options.shouldSkipDetailWalk(card, rawId)) {
      consecutiveSkips += 1;
      if (consecutiveSkips >= MAX_CONSECUTIVE_DETAIL_SKIPS) {
        break;
      }
      continue;
    }
    const currentDetail = options.captureDetail('', { useHint: false });
    if (currentDetail?.title && normalizeText(currentDetail.raw_id) === rawId) {
      detailMap.set(rawId, currentDetail);
      consecutiveSkips = 0;
      continue;
    }
    const clickTarget = options.getClickTarget(card);
    if (!clickTarget) {
      consecutiveSkips += 1;
      if (consecutiveSkips >= MAX_CONSECUTIVE_DETAIL_SKIPS) {
        break;
      }
      continue;
    }
    safeScrollIntoView(card);
    const previousSignature = detailSignature(options.captureDetail('', { useHint: false }));
    clickElement(clickTarget);
    const detail = await waitForDetailChange(rawId, previousSignature, options.captureDetail);
    if (detail?.title) {
      detailMap.set(rawId, detail);
      consecutiveSkips = 0;
    } else {
      consecutiveSkips += 1;
      if (consecutiveSkips >= MAX_CONSECUTIVE_DETAIL_SKIPS) {
        break;
      }
    }
  }
  return detailMap;
}

async function waitForDetailChange(rawId, previousSignature, captureDetail) {
  const deadline = Date.now() + DETAIL_CAPTURE_TIMEOUT_MS;
  while (Date.now() < deadline) {
    await delay(DETAIL_CAPTURE_POLL_MS);
    const detail = captureDetail('', { useHint: false });
    if (!detail?.title) {
      continue;
    }
    if (rawId && normalizeText(detail.raw_id) === normalizeText(rawId)) {
      return detail;
    }
    const signature = detailSignature(detail);
    if (signature && signature !== previousSignature) {
      if (!normalizeText(detail.raw_id) && rawId) {
        return { ...detail, raw_id: rawId };
      }
      return detail;
    }
  }
  const fallbackDetail = captureDetail(rawId, { useHint: true });
  if (fallbackDetail?.title && detailSignature(fallbackDetail) !== previousSignature) {
    return fallbackDetail;
  }
  return null;
}

function looksLikeJobUrl(url) {
  return /job|career|opening|position|posting|opportunit/i.test(url);
}

function jobIdFromUrl(url) {
  if (!url) {
    return '';
  }
  const linkedInMatch = url.match(/\/jobs\/view\/(\d+)/i);
  if (linkedInMatch) {
    return linkedInMatch[1];
  }
  const indeedMatch = url.match(/[?&](?:jk|currentJobId)=([^&]+)/i);
  if (indeedMatch) {
    return indeedMatch[1];
  }
  return '';
}

function canonicalIndeedJobUrl(href, rawId) {
  const resolved = absoluteUrl(href || '');
  let jobId = normalizeText(rawId);
  try {
    const url = new URL(resolved || location.href, location.href);
    for (const key of ['jk', 'currentJobId', 'vjk']) {
      const value = normalizeText(url.searchParams.get(key) || '');
      if (value) {
        jobId = value;
        break;
      }
    }
    if (url.hostname.toLowerCase().includes('indeed.') && jobId) {
      return `${url.protocol}//${url.host}/viewjob?jk=${encodeURIComponent(jobId)}`;
    }
    return resolved;
  } catch (_error) {
    return resolved;
  }
}

function looksLikeGroupedOpeningsCard(node) {
  const value = normalizeText([
    firstText(node, ['[data-testid="attribute_snippet_testid"]']),
    text(node),
  ].join(' ')).toLowerCase();
  if (!value) {
    return false;
  }
  return /\bmultiple openings\b|\bmultiple jobs\b|\bhiring multiple\b|\bseveral openings\b|\bsee all\b/.test(value);
}

function siteNameFromHost(host) {
  const folded = String(host || '').toLowerCase();
  if (folded.includes('linkedin')) {
    return 'LinkedIn';
  }
  if (folded.includes('indeed')) {
    return 'Indeed';
  }
  return normalizeText(folded.replace(/^www\./, '').split('.')[0].replace(/-/g, ' ')) || 'Web';
}

function absoluteUrl(href) {
  try {
    return new URL(href, location.href).toString();
  } catch (_error) {
    return '';
  }
}

function firstNode(root, selectors) {
  for (const selector of selectors) {
    const node = root.querySelector(selector);
    if (node) {
      return node;
    }
  }
  return null;
}

function firstText(root, selectors) {
  const node = firstNode(root, selectors);
  return text(node);
}

function texts(root, selectors) {
  const values = [];
  const seen = new Set();
  for (const selector of selectors) {
    for (const node of root.querySelectorAll(selector)) {
      const value = text(node);
      if (!value) {
        continue;
      }
      const folded = value.toLowerCase();
      if (seen.has(folded)) {
        continue;
      }
      seen.add(folded);
      values.push(value);
    }
  }
  return values;
}

function firstSalaryText(values) {
  const ranked = values
    .filter(Boolean)
    .map((value) => ({ value, score: salaryScore(value) }))
    .sort((left, right) => right.score - left.score || right.value.length - left.value.length);
  return ranked[0]?.value || '';
}

function text(node) {
  if (!node) {
    return '';
  }
  const value = node instanceof HTMLElement
    ? (node.innerText || node.textContent || '')
    : (node.textContent || '');
  return normalizeText(value);
}

function normalizeText(value) {
  return String(value || '').replace(/\s+/g, ' ').trim();
}

function looksLikeSalaryText(value) {
  const textValue = normalizeText(value);
  if (!textValue) {
    return false;
  }
  if (/[$€£]|usd/i.test(textValue)) {
    return true;
  }
  return /\b(?:salary|compensation|pay|hourly|annual|year|month|week|day|hr|yr)\b/i.test(textValue);
}

function salaryScore(value) {
  const textValue = normalizeText(value);
  if (!textValue) {
    return 0;
  }
  let score = 0;
  if (looksLikeSalaryText(textValue)) {
    score += 3;
  }
  if (/[$â‚¬Â£]|usd/i.test(textValue)) {
    score += 8;
  }
  if (/\b(?:salary|compensation|pay(?: range)?|hourly|annual|per hour|per year|a year|an hour)\b/i.test(textValue)) {
    score += 5;
  }
  if (/\d[\d,.]*(?:\s*[km])?\s*(?:-|to|and|through)\s*[$â‚¬Â£]?\s*\d/i.test(textValue)) {
    score += 8;
  }
  if (/\b(?:per hour|an hour|hourly|per year|a year|annual(?:ly)?|per month|monthly|per week|weekly|per day|daily|\/hr|\/yr|\/wk|\/mo|\/day)\b/i.test(textValue)) {
    score += 6;
  }
  if (/\b\d+(?:\.\d+)?\s*(?:-|to|and|through)\s*\d+(?:\.\d+)?\s+years?\b/i.test(textValue) && !/[$â‚¬Â£]|usd/i.test(textValue)) {
    score -= 12;
  }
  return score + Math.min(textValue.length, 120) / 40;
}

function excerpt(value, limit) {
  const normalized = normalizeText(value);
  if (normalized.length <= limit) {
    return normalized;
  }
  return `${normalized.slice(0, Math.max(limit - 1, 0)).trimEnd()}…`;
}

function jsonLdSalaryText(payload) {
  const baseSalary = payload?.baseSalary;
  if (!baseSalary || typeof baseSalary !== 'object') {
    return '';
  }
  const value = baseSalary.value;
  if (!value || typeof value !== 'object') {
    return '';
  }
  const minValue = Number(value.minValue ?? value.value ?? NaN);
  const maxValue = Number(value.maxValue ?? value.value ?? NaN);
  if (!Number.isFinite(minValue) && !Number.isFinite(maxValue)) {
    return '';
  }
  const currency = String(baseSalary.currency || 'USD').toUpperCase();
  const prefix = currency === 'USD' ? '$' : `${currency} `;
  const left = Number.isFinite(minValue) ? formatSalaryAmount(minValue) : '';
  const right = Number.isFinite(maxValue) ? formatSalaryAmount(maxValue) : left;
  let display = left && right && left !== right ? `${prefix}${left} - ${prefix}${right}` : `${prefix}${left || right}`;
  const unit = String(value.unitText || '').toLowerCase();
  if (unit.includes('hour')) {
    display += '/hr';
  } else if (unit.includes('week')) {
    display += '/wk';
  } else if (unit.includes('month')) {
    display += '/mo';
  } else if (unit.includes('day')) {
    display += '/day';
  } else if (unit.includes('year') || unit.includes('annual')) {
    display += '/yr';
  }
  return display;
}

function formatSalaryAmount(value) {
  if (!Number.isFinite(value)) {
    return '';
  }
  if (value >= 1000) {
    return Math.round(value).toLocaleString('en-US');
  }
  if (Math.floor(value) === value) {
    return String(value);
  }
  return value.toFixed(2);
}

function uniqueNodes(nodes) {
  const seen = new Set();
  const output = [];
  for (const node of nodes) {
    if (!node || seen.has(node)) {
      continue;
    }
    seen.add(node);
    output.push(node);
  }
  return output;
}

function isVisible(node) {
  if (!(node instanceof Element)) {
    return false;
  }
  const style = window.getComputedStyle(node);
  if (style.display === 'none' || style.visibility === 'hidden' || Number(style.opacity) === 0) {
    return false;
  }
  const rect = node.getBoundingClientRect();
  return rect.width > 0 && rect.height > 0;
}

function safeScrollIntoView(node) {
  try {
    node.scrollIntoView({ block: 'center', inline: 'nearest', behavior: 'auto' });
  } catch (_error) {
    node.scrollIntoView();
  }
}

function clickElement(node) {
  node.dispatchEvent(new MouseEvent('mouseover', { bubbles: true, cancelable: true, view: window }));
  node.dispatchEvent(new MouseEvent('mousedown', { bubbles: true, cancelable: true, view: window }));
  node.dispatchEvent(new MouseEvent('mouseup', { bubbles: true, cancelable: true, view: window }));
  node.dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true, view: window }));
}

function detailSignature(detail) {
  if (!detail) {
    return '';
  }
  return normalizeText([
    detail.raw_id || '',
    detail.title || '',
    excerpt(detail.description || detail.summary || '', 180),
  ].join('|'));
}

function delay(ms) {
  return new Promise((resolve) => window.setTimeout(resolve, ms));
}
