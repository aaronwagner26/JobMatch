chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
  if (message?.type !== 'jobmatch_capture_page') {
    return undefined;
  }

  try {
    sendResponse({ ok: true, payload: captureCurrentPage() });
  } catch (error) {
    sendResponse({
      ok: false,
      error: error instanceof Error ? error.message : String(error),
    });
  }
  return true;
});

function captureCurrentPage() {
  const host = location.hostname.toLowerCase();
  if (host.endsWith('linkedin.com') && location.pathname.toLowerCase().includes('/company/') && location.pathname.toLowerCase().includes('/jobs')) {
    return captureLinkedInCompanyPage();
  }
  if (host.includes('indeed.')) {
    return captureIndeedPage();
  }
  return captureGenericPage();
}

function captureLinkedInCompanyPage() {
  const company = firstText(document, [
    '.org-top-card-summary__title',
    '.job-details-jobs-unified-top-card__company-name',
    '.jobs-company__name',
    'h1',
  ]);
  const detail = captureLinkedInSelectedDetail();
  const cards = Array.from(
    document.querySelectorAll('[data-job-id], li.jobs-search-results__list-item, li.scaffold-layout__list-item')
  );
  const jobs = uniqueJobs(cards.map((card) => {
    const anchor = firstNode(card, [
      'a[href*="/jobs/view/"]',
      'a.job-card-list__title',
      'a.job-card-container__link',
    ]);
    const url = absoluteUrl(anchor?.href || '');
    const rawId = card.getAttribute('data-job-id') || anchor?.getAttribute('data-job-id') || jobIdFromUrl(url);
    const selected = detail && rawId && detail.raw_id === rawId ? detail : null;
    return {
      raw_id: rawId || url,
      title: firstText(card, ['.job-card-list__title', '.job-card-container__link', 'strong']) || selected?.title || text(anchor),
      company: company || selected?.company || firstText(card, ['.artdeco-entity-lockup__subtitle', '.job-card-container__company-name']),
      location: firstText(card, ['.job-card-container__metadata-item', '.artdeco-entity-lockup__caption']) || selected?.location || '',
      summary: firstText(card, ['.job-card-container__footer-job-state', '.job-card-list__footer-wrapper']) || '',
      description: selected?.description || '',
      url: selected?.url || url,
    };
  }));
  return buildCapturePayload('linkedin_company', 'LinkedIn', company, jobs);
}

function captureLinkedInSelectedDetail() {
  const url = absoluteUrl(
    firstNode(document, [
      'a[href*="/jobs/view/"].job-details-jobs-unified-top-card__job-title-link',
      '.jobs-unified-top-card__content a[href*="/jobs/view/"]',
      'a[href*="/jobs/view/"]',
    ])?.href || location.href
  );
  const rawId = jobIdFromUrl(url) || jobIdFromUrl(location.href);
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
    description: firstText(document, [
      '.jobs-description-content__text',
      '.jobs-box__html-content',
      '.jobs-description__content',
    ]),
    url,
  };
}

function captureIndeedPage() {
  const cards = Array.from(document.querySelectorAll('[data-jk], [data-testid="slider_item"], a[href*="/viewjob"]'));
  const jobs = uniqueJobs(cards.map((node) => {
    const anchor = node.tagName === 'A' ? node : firstNode(node, ['a[href*="/viewjob"]', 'a[href*="jk="]']);
    const container = node.tagName === 'A' ? node.closest('[data-jk], [data-testid="slider_item"], article, li, div') || node : node;
    const url = absoluteUrl(anchor?.href || '');
    const rawId = container?.getAttribute?.('data-jk') || anchor?.getAttribute?.('data-jk') || jobIdFromUrl(url);
    return {
      raw_id: rawId || url,
      title: text(anchor),
      company: firstText(container, ['.companyName', '[data-testid="company-name"]']),
      location: firstText(container, ['.companyLocation', '[data-testid="text-location"]']),
      summary: firstText(container, ['.job-snippet', '[data-testid="job-snippet"]']),
      description: '',
      url,
      posted_at: firstText(container, ['.date', 'time']),
    };
  }));
  const queryLabel = firstText(document, ['h1', '[data-testid="jobsearch-HeroLabel"]']) || document.title;
  return buildCapturePayload('indeed_search', 'Indeed', queryLabel, jobs);
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

function text(node) {
  return normalizeText(node?.textContent || '');
}

function normalizeText(value) {
  return String(value || '').replace(/\s+/g, ' ').trim();
}
