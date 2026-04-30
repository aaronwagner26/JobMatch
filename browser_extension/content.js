const DETAIL_CAPTURE_LIMIT = 24;
const DETAIL_CAPTURE_TIMEOUT_MS = 1600;
const DETAIL_CAPTURE_POLL_MS = 140;
const MAX_CONSECUTIVE_DETAIL_SKIPS = 3;

chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
  if (message?.type === 'jobmatch_capture_page') {
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
  }

  if (message?.type === 'jobmatch_fill_application') {
    void (async () => {
      try {
        const result = fillApplicationFields(message.profile || {}, { mode: message.mode || 'common' });
        sendResponse({ ok: true, ...result });
      } catch (error) {
        sendResponse({
          ok: false,
          error: error instanceof Error ? error.message : String(error),
        });
      }
    })();
    return true;
  }

  return undefined;
});

async function captureCurrentPage() {
  const host = location.hostname.toLowerCase();
  if (host.endsWith('linkedin.com') && location.pathname.toLowerCase().includes('/company/') && location.pathname.toLowerCase().includes('/jobs')) {
    return captureLinkedInCompanyPage();
  }
  if (host.includes('indeed.')) {
    return captureIndeedPage();
  }
  if (host.includes('clearancejobs.com')) {
    return captureClearanceJobsPage();
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
    getExpectedTitle: (node) => {
      const anchor = node.tagName === 'A' ? node : firstNode(node, ['a[href*="/viewjob"]', 'a[href*="jk="]', 'a[href*="/rc/clk"]', 'a[href*="/pagead/clk"]']);
      return text(anchor);
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
    const cardMetaTexts = indeedCardMetaTexts(container);
    const cardSalaryText = firstSalaryText([
      ...texts(container, ['.salary-snippet', '.estimated-salary', '[class*="salary"]', '[data-testid="attribute_snippet_testid"]']),
      ...cardMetaTexts,
    ]);
    const cardEmploymentText = cardMetaTexts.filter((value) => value && value !== cardSalaryText && looksLikeEmploymentText(value)).join(' | ');
    return {
      raw_id: rawId || url,
      title: text(anchor),
      company: firstText(container, ['.companyName', '[data-testid="company-name"]']),
      location: firstText(container, ['.companyLocation', '[data-testid="text-location"]']),
      summary: selected?.summary || firstText(container, ['.job-snippet', '[data-testid="job-snippet"]']),
      description: selected?.description || '',
      salary_text: cardSalaryText || selected?.salary_text || '',
      employment_text: selected?.employment_text || cardEmploymentText || '',
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

async function captureClearanceJobsPage() {
  const cards = uniqueNodes(
    Array.from(document.querySelectorAll('.job-search-list-item-desktop')).filter((node) => isVisible(node))
  );
  const detail = captureClearanceJobsDetailPage();
  if (cards.length === 0 && detail) {
    return buildCapturePayload('clearance_job', 'ClearanceJobs', detail.company || detail.title, [detail]);
  }
  const jobs = uniqueJobs(cards.map((card) => {
    const anchor = firstNode(card, ['a.job-search-list-item-desktop__job-name', 'a[href*="/jobs/"]']);
    const url = absoluteUrl(anchor?.href || '');
    if (!anchor || !url) {
      return null;
    }
    const locationText = firstText(card, ['.job-search-list-item-desktop__location', '.location']);
    const summary = firstText(card, ['.job-search-list-item-desktop__description', '.job-description', 'p']);
    const footerTexts = clearanceCardMetaTexts(card);
    const postedAt = footerTexts.find((value) => /^posted\b/i.test(value)) || '';
    return {
      raw_id: url,
      title: text(anchor),
      company: firstText(card, ['.job-search-list-item-desktop__company-name', '.company']),
      location: locationText,
      summary,
      description: '',
      requirements_text: joinUniqueTexts([
        summary,
        ...footerTexts.filter((value) => value && value !== postedAt && normalizeText(value).toLowerCase() !== normalizeText(locationText).toLowerCase()),
      ]),
      salary_text: firstMeaningfulSalaryText([
        summary,
        ...footerTexts,
        ...texts(card, ['.salary', '.salary-range', '.compensation', '[class*="salary"]']),
      ]),
      employment_text: joinUniqueTexts(
        footerTexts.filter((value) => looksLikeEmploymentText(value) && normalizeText(value).toLowerCase() !== normalizeText(locationText).toLowerCase())
      ),
      url,
      posted_at: postedAt,
    };
  }));
  if (jobs.length === 0 && detail) {
    jobs.push(detail);
  }
  const label = firstText(document, ['h1', '.job-search-title']) || document.title;
  return buildCapturePayload('clearance_search', 'ClearanceJobs', label, jobs);
}

function captureClearanceJobsDetailPage() {
  const title = firstText(document, [
    'h1.job-view-header-content__top__job-name',
    '.job-view-header-content__top__job-name',
    'h1',
  ]);
  if (!title) {
    return null;
  }
  const description = firstText(document, ['.job-description']) || '';
  const requirementsText = firstText(document, ['.job-info']) || '';
  const locationText = firstText(document, [
    '.job-info .job-fit__nonSkills--location .el-tag__content',
    '.job-view-header-content__top__location',
  ]);
  const salaryText = firstMeaningfulSalaryText([
    ...texts(document, [
      '.job-info .job-fit__nonSkills--salary .el-tag__content',
      '.job-info .salary-estimate-link',
      '.job-info [class*="salary"]',
    ]),
    requirementsText,
    description,
  ]);
  return {
    raw_id: absoluteUrl(location.href),
    title,
    company: firstText(document, [
      'h2.job-view-header-content__top__job-company',
      '.job-view-header-content__top__job-company a',
      '.job-view-header-content__top__job-company',
    ]),
    location: locationText,
    summary: excerpt(description, 280),
    description,
    requirements_text: requirementsText,
    salary_text: salaryText,
    employment_text: joinUniqueTexts(
      texts(document, [
        '.job-info .job-fit__nonSkills--careerLevel .el-tag__content',
        '.job-info .job-fit__nonSkills--location .el-tag__content',
      ]).filter((value) => value && value !== salaryText && normalizeText(value).toLowerCase() !== normalizeText(locationText).toLowerCase() && !looksLikeClearanceText(value) && !looksLikePlaceholderMeta(value))
    ),
    url: absoluteUrl(location.href),
    posted_at: firstText(document, ['time', '.posted-date']),
  };
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
  const explicitSalaryCandidates = texts(document, [
    '#jobDetailsSection [aria-label="Pay"] li',
    '#jobDetailsSection [aria-label="Pay"] [data-testid="list-item"]',
    '#jobDetailsSection [aria-label="Pay"] span',
    '#salaryInfoAndJobType',
    '[data-testid="salaryInfoAndJobType"]',
    '[data-testid="jobsearch-CollapsedEmbeddedHeader-salary"]',
    '.salaryText',
  ]);
  const fallbackSalaryCandidates = texts(document, [
    '.jobsearch-OtherJobDetailsContainer',
    '[data-testid="attribute_snippet_testid"]',
    '[class*="salary"]',
  ]);
  const salaryCandidates = [
    ...(explicitSalaryCandidates.length ? explicitSalaryCandidates : fallbackSalaryCandidates),
    jsonLdMatch?.salary_text || '',
  ].filter(Boolean);
  const salaryText = firstSalaryText(salaryCandidates);
  const employmentText = texts(document, [
    '#jobDetailsSection [aria-label="Job type"] li',
    '#jobDetailsSection [aria-label="Job type"] [data-testid="list-item"]',
    '#jobDetailsSection [aria-label="Work setting"] li',
    '#jobDetailsSection [aria-label="Work setting"] [data-testid="list-item"]',
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
    const expectedTitle = normalizeText(typeof options.getExpectedTitle === 'function' ? options.getExpectedTitle(card) : '');
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
    if (
      currentDetail?.title
      && (
        normalizeText(currentDetail.raw_id) === rawId
        || titlesProbablyMatch(currentDetail.title, expectedTitle)
      )
    ) {
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
    const detail = await waitForDetailChange(rawId, expectedTitle, previousSignature, options.captureDetail);
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

async function waitForDetailChange(rawId, expectedTitle, previousSignature, captureDetail) {
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
    if (signature && signature !== previousSignature && titlesProbablyMatch(detail.title, expectedTitle)) {
      return detail;
    }
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

function indeedCardMetaTexts(container) {
  return texts(container, [
    '[data-testid="attribute_snippet_testid"]',
    '[data-testid*="attribute"]',
    '[class*="metadata"] li',
    '[class*="metadata"] span',
    '.metadata li',
    '.metadata span',
    'ul li',
  ]).filter((value) => isCompactCardMeta(value));
}

function clearanceCardMetaTexts(container) {
  const values = [];
  const seen = new Set();
  for (const group of container.querySelectorAll('.job-search-list-item-desktop__footer > div')) {
    const nodes = Array.from(group.children).filter((node) => node instanceof HTMLElement);
    const candidates = nodes.length ? nodes : [group];
    for (const node of candidates) {
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

function isCompactCardMeta(value) {
  const textValue = normalizeText(value);
  if (!textValue) {
    return false;
  }
  if (textValue.length > 96) {
    return false;
  }
  return true;
}

function looksLikeEmploymentText(value) {
  return /\b(?:contract|full[\s-]?time|part[\s-]?time|temporary|temp[\s-]?to[\s-]?hire|internship|day shift|night shift|overnight|weekends?|overtime|monday to friday|remote|hybrid|on[\s-]?site)\b/i.test(normalizeText(value));
}

function looksLikePlaceholderMeta(value) {
  const textValue = normalizeText(value).toLowerCase();
  if (!textValue) {
    return true;
  }
  return [
    'unspecified',
    'not specified',
    'salary not specified',
    'clearance unspecified',
    'career level not specified',
  ].includes(textValue);
}

function looksLikeClearanceText(value) {
  return /\b(?:clearance|polygraph|public trust|top secret|secret|ts\/sci|sci|ci poly|full scope)\b/i.test(normalizeText(value));
}

function joinUniqueTexts(values) {
  const output = [];
  const seen = new Set();
  for (const value of values) {
    const normalized = normalizeText(value);
    if (!normalized) {
      continue;
    }
    const folded = normalized.toLowerCase();
    if (seen.has(folded)) {
      continue;
    }
    seen.add(folded);
    output.push(normalized);
  }
  return output.join(' | ');
}

function firstMeaningfulSalaryText(values) {
  return firstSalaryText(values.filter((value) => value && !looksLikePlaceholderMeta(value)));
}

function titlesProbablyMatch(left, right) {
  const a = normalizeTitleForMatch(left);
  const b = normalizeTitleForMatch(right);
  if (!a || !b) {
    return false;
  }
  return a === b || a.includes(b) || b.includes(a);
}

function normalizeTitleForMatch(value) {
  return normalizeText(value).toLowerCase().replace(/[^a-z0-9]+/g, ' ').trim();
}

function siteNameFromHost(host) {
  const folded = String(host || '').toLowerCase();
  if (folded.includes('linkedin')) {
    return 'LinkedIn';
  }
  if (folded.includes('indeed')) {
    return 'Indeed';
  }
  if (folded.includes('clearancejobs')) {
    return 'ClearanceJobs';
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
    .sort((left, right) => right.score - left.score || left.value.length - right.value.length);
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
  const employmentNoise = /\b(?:contract|full[\s-]?time|part[\s-]?time|temporary|temp[\s-]?to[\s-]?hire|day shift|night shift|weekends?|overtime|remote|hybrid|on[\s-]?site|work setting)\b/i.test(textValue);
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
  if (employmentNoise) {
    score -= 6;
  }
  if (!employmentNoise && !/\|/.test(textValue)) {
    score += 2;
  }
  return score;
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

function fillApplicationFields(profile, { mode = 'common' } = {}) {
  const basics = profile?.basics || {};
  const workHistory = Array.isArray(profile?.work_history) ? profile.work_history : [];
  const education = Array.isArray(profile?.education) ? profile.education : [];
  const targets = mode === 'focused'
    ? [document.activeElement].filter((element) => isFillableField(element))
    : Array.from(document.querySelectorAll('input, textarea, select')).filter((element) => isFillableField(element));
  if (targets.length === 0) {
    throw new Error(mode === 'focused' ? 'Focus a form field before using this action.' : 'No fillable fields were detected on this page.');
  }

  const counters = {};
  let filled = 0;
  const preview = [];
  for (const element of targets) {
    const context = classifyApplicationField(element);
    if (!context) {
      continue;
    }
    const value = valueForApplicationField(context, element, basics, workHistory, education, profile, counters);
    if (!value) {
      continue;
    }
    if (mode !== 'focused' && hasMeaningfulFieldValue(element)) {
      continue;
    }
    if (applyFieldValue(element, value)) {
      filled += 1;
      if (preview.length < 5) {
        preview.push(context.label);
      }
    }
  }
  if (filled === 0) {
    throw new Error(mode === 'focused' ? 'Could not map the focused field to a structured resume value.' : 'No recognizable empty fields matched the structured resume profile.');
  }
  return { filled, preview: preview.join(', ') };
}

function classifyApplicationField(element) {
  const context = fieldContextText(element);
  if (!context) {
    return null;
  }
  if (/\b(cover letter|why do you want|why are you|additional information|motivation|tell us why)\b/i.test(context)) {
    return null;
  }
  const autocomplete = normalizeText(element.getAttribute('autocomplete') || '').toLowerCase();
  if (autocomplete === 'name') return { kind: 'full_name', label: 'full name' };
  if (autocomplete === 'given-name') return { kind: 'first_name', label: 'first name' };
  if (autocomplete === 'family-name') return { kind: 'last_name', label: 'last name' };
  if (autocomplete === 'email') return { kind: 'email', label: 'email' };
  if (autocomplete === 'tel') return { kind: 'phone', label: 'phone' };
  if (autocomplete === 'url') return { kind: 'website_url', label: 'website' };
  if (autocomplete === 'postal-code') return { kind: 'postal_code', label: 'postal code' };
  if (autocomplete === 'address-level2') return { kind: 'city', label: 'city' };
  if (autocomplete === 'address-level1') return { kind: 'state', label: 'state' };

  if (/\b(full name|legal name|applicant name|candidate name)\b/i.test(context)) return { kind: 'full_name', label: 'full name' };
  if (/\b(first name|given name)\b/i.test(context)) return { kind: 'first_name', label: 'first name' };
  if (/\b(last name|family name|surname)\b/i.test(context)) return { kind: 'last_name', label: 'last name' };
  if (/\bemail\b/i.test(context)) return { kind: 'email', label: 'email' };
  if (/\b(phone|mobile|cell)\b/i.test(context)) return { kind: 'phone', label: 'phone' };
  if (/\blinkedin\b/i.test(context)) return { kind: 'linkedin_url', label: 'linkedin' };
  if (/\b(portfolio|website|personal site|github url|homepage)\b/i.test(context)) return { kind: 'website_url', label: 'website' };
  if (/\b(zip|postal code)\b/i.test(context)) return { kind: 'postal_code', label: 'postal code' };
  if (/\bcity\b/i.test(context)) return { kind: 'city', label: 'city' };
  if (/\bstate|province|region\b/i.test(context)) return { kind: 'state', label: 'state' };
  if (/\b(address|location)\b/i.test(context)) return { kind: 'location', label: 'location' };
  if (/\b(years? of experience|experience years?)\b/i.test(context)) return { kind: 'years_experience', label: 'years of experience' };
  if (/\b(professional summary|summary|about you|bio)\b/i.test(context)) return { kind: 'summary', label: 'summary' };
  if (/\bheadline|current title|professional title\b/i.test(context)) return { kind: 'headline', label: 'headline' };
  if (/\bskills?|technologies|technical skills\b/i.test(context)) return { kind: 'skills', label: 'skills' };
  if (/\btools?|platforms?\b/i.test(context)) return { kind: 'tools', label: 'tools' };
  if (/\bcertifications?\b/i.test(context)) return { kind: 'certifications', label: 'certifications' };
  if (/\bclearance|public trust|polygraph\b/i.test(context)) return { kind: 'clearance', label: 'clearance' };
  if (/\b(company|employer|organization)\b/i.test(context) && /\b(work|employment|experience|current|previous|most recent)\b/i.test(context)) return { kind: 'job_company', label: 'job company' };
  if (/\b(job title|title|position|role)\b/i.test(context) && /\b(work|employment|experience|current|previous|most recent)\b/i.test(context)) return { kind: 'job_title', label: 'job title' };
  if (/\b(work|employment|experience).*\blocation\b|\blocation\b.*\b(work|employment|experience)\b/i.test(context)) return { kind: 'job_location', label: 'job location' };
  if (/\b(start date|date started|from date)\b/i.test(context) && /\b(work|employment|experience)\b/i.test(context)) return { kind: 'job_start_date', label: 'job start date' };
  if (/\b(end date|date ended|to date)\b/i.test(context) && /\b(work|employment|experience)\b/i.test(context)) return { kind: 'job_end_date', label: 'job end date' };
  if (/\b(description|responsibilities|achievements)\b/i.test(context) && /\b(work|employment|experience)\b/i.test(context)) return { kind: 'job_description', label: 'job description' };
  if (/\b(school|university|college)\b/i.test(context)) return { kind: 'edu_school', label: 'school' };
  if (/\b(degree|credential)\b/i.test(context)) return { kind: 'edu_degree', label: 'degree' };
  if (/\b(field of study|major|concentration)\b/i.test(context)) return { kind: 'edu_field', label: 'field of study' };
  if (/\b(start date|from date)\b/i.test(context) && /\b(education|school|degree)\b/i.test(context)) return { kind: 'edu_start_date', label: 'education start date' };
  if (/\b(end date|graduation|graduated|to date)\b/i.test(context) && /\b(education|school|degree)\b/i.test(context)) return { kind: 'edu_end_date', label: 'education end date' };
  return null;
}

function valueForApplicationField(context, element, basics, workHistory, education, profile, counters) {
  switch (context.kind) {
    case 'full_name':
      return basics.full_name || '';
    case 'first_name':
      return splitName(basics.full_name).first;
    case 'last_name':
      return splitName(basics.full_name).last;
    case 'email':
      return basics.email || '';
    case 'phone':
      return basics.phone || '';
    case 'location':
      return basics.location || '';
    case 'city':
      return locationParts(basics.location).city;
    case 'state':
      return locationParts(basics.location).state;
    case 'postal_code':
      return locationParts(basics.location).postalCode;
    case 'linkedin_url':
      return basics.linkedin_url || '';
    case 'website_url':
      return basics.website_url || '';
    case 'summary':
      return basics.summary || '';
    case 'headline':
      return basics.headline || '';
    case 'years_experience':
      return String(basics.years_experience || profile.experience_years || '');
    case 'skills':
      return Array.isArray(profile.skills) ? profile.skills.join(', ') : '';
    case 'tools':
      return Array.isArray(profile.tools) ? profile.tools.join(', ') : '';
    case 'certifications':
      return Array.isArray(profile.certifications) ? profile.certifications.join(', ') : '';
    case 'clearance':
      return Array.isArray(profile.clearance_terms) ? profile.clearance_terms.join(', ') : '';
    case 'job_title':
      return workHistory[consumeIndex(counters, 'job_title', element)]?.title || '';
    case 'job_company':
      return workHistory[consumeIndex(counters, 'job_company', element)]?.company || '';
    case 'job_location':
      return workHistory[consumeIndex(counters, 'job_location', element)]?.location || '';
    case 'job_start_date':
      return formatApplicationDate(workHistory[consumeIndex(counters, 'job_start_date', element)]?.start_date || '', element);
    case 'job_end_date':
      return formatApplicationDate(workHistory[consumeIndex(counters, 'job_end_date', element)]?.end_date || '', element);
    case 'job_description':
      return workHistory[consumeIndex(counters, 'job_description', element)]?.description || '';
    case 'edu_school':
      return education[consumeIndex(counters, 'edu_school', element)]?.school || '';
    case 'edu_degree':
      return education[consumeIndex(counters, 'edu_degree', element)]?.degree || '';
    case 'edu_field':
      return education[consumeIndex(counters, 'edu_field', element)]?.field_of_study || '';
    case 'edu_start_date':
      return formatApplicationDate(education[consumeIndex(counters, 'edu_start_date', element)]?.start_date || '', element);
    case 'edu_end_date':
      return formatApplicationDate(education[consumeIndex(counters, 'edu_end_date', element)]?.end_date || '', element);
    default:
      return '';
  }
}

function fieldContextText(element) {
  const bits = [
    normalizeText(element.getAttribute('aria-label') || ''),
    normalizeText(element.getAttribute('placeholder') || ''),
    normalizeText(element.getAttribute('name') || ''),
    normalizeText(element.getAttribute('id') || ''),
    normalizeText(element.getAttribute('autocomplete') || ''),
  ];
  if (element.labels) {
    bits.push(...Array.from(element.labels).map((label) => text(label)));
  }
  const closestLabel = element.closest('label');
  if (closestLabel) {
    bits.push(text(closestLabel));
  }
  const container = element.closest('fieldset, [role="group"], .field, .form-group, .application-question, .question, .input-wrapper, .jobs-easy-apply-form-section__grouping');
  if (container) {
    bits.push(firstText(container, ['legend', 'label', 'h2', 'h3', '.question-label', '.field-label', '.artdeco-text-input--label']));
  }
  return normalizeText(bits.filter(Boolean).join(' | '));
}

function consumeIndex(counters, kind, element) {
  const explicitIndex = inferFieldIndex(element);
  if (explicitIndex !== null) {
    return explicitIndex;
  }
  const current = counters[kind] || 0;
  counters[kind] = current + 1;
  return current;
}

function inferFieldIndex(element) {
  const textValue = normalizeText([
    element.getAttribute('name') || '',
    element.getAttribute('id') || '',
    element.getAttribute('data-testid') || '',
  ].join(' '));
  const bracketMatch = textValue.match(/\[(\d+)\]/);
  if (bracketMatch) {
    return Number(bracketMatch[1]);
  }
  const digitMatch = textValue.match(/(?:experience|employment|work|education|school)[^\d]{0,8}(\d+)/i);
  if (digitMatch) {
    return Number(digitMatch[1]);
  }
  return null;
}

function applyFieldValue(element, value) {
  if (!value) {
    return false;
  }
  if (element instanceof HTMLSelectElement) {
    const option = Array.from(element.options).find((item) => {
      const haystack = normalizeText(`${item.textContent || ''} ${item.value || ''}`).toLowerCase();
      return haystack && haystack.includes(normalizeText(value).toLowerCase());
    });
    if (option) {
      element.value = option.value;
    } else {
      return false;
    }
  } else {
    element.focus();
    element.value = value;
  }
  element.dispatchEvent(new Event('input', { bubbles: true }));
  element.dispatchEvent(new Event('change', { bubbles: true }));
  element.dispatchEvent(new Event('blur', { bubbles: true }));
  return true;
}

function hasMeaningfulFieldValue(element) {
  if (!(element instanceof HTMLInputElement || element instanceof HTMLTextAreaElement || element instanceof HTMLSelectElement)) {
    return false;
  }
  return normalizeText(element.value || '') !== '';
}

function isFillableField(element) {
  if (!(element instanceof HTMLInputElement || element instanceof HTMLTextAreaElement || element instanceof HTMLSelectElement)) {
    return false;
  }
  if (!isVisible(element) || element.disabled || element.readOnly) {
    return false;
  }
  if (element instanceof HTMLInputElement) {
    const blocked = new Set(['hidden', 'file', 'checkbox', 'radio', 'submit', 'button', 'password']);
    if (blocked.has((element.type || '').toLowerCase())) {
      return false;
    }
  }
  return true;
}

function splitName(fullName) {
  const parts = normalizeText(fullName).split(' ').filter(Boolean);
  if (parts.length === 0) {
    return { first: '', last: '' };
  }
  if (parts.length === 1) {
    return { first: parts[0], last: '' };
  }
  return { first: parts[0], last: parts.slice(1).join(' ') };
}

function locationParts(value) {
  const normalized = normalizeText(value);
  const postalMatch = normalized.match(/\b\d{5}(?:-\d{4})?\b/);
  const postalCode = postalMatch ? postalMatch[0] : '';
  const parts = normalized.split(',').map((part) => normalizeText(part));
  let city = parts[0] || '';
  let state = '';
  if (parts.length > 1) {
    const statePart = parts[1].replace(postalCode, '').trim();
    state = statePart.split(/\s+/)[0] || '';
  }
  return { city, state, postalCode };
}

function formatApplicationDate(value, element) {
  const normalized = normalizeText(value);
  if (!normalized) {
    return '';
  }
  if (element instanceof HTMLInputElement && element.type === 'month') {
    return normalized.slice(0, 7);
  }
  if (element instanceof HTMLInputElement && element.type === 'date') {
    return normalized.slice(0, 10);
  }
  return normalized;
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
    detail.title || '',
    detail.company || '',
    detail.location || '',
    detail.salary_text || '',
    excerpt(detail.description || detail.summary || '', 180),
  ].join('|'));
}

function delay(ms) {
  return new Promise((resolve) => window.setTimeout(resolve, ms));
}
