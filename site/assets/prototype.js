(() => {
  'use strict';
  const legacyResources = window.ALOGS_DRAFT_DATA.resources;
  const D = window.ALOGS_LIVE_DATA || window.ALOGS_DRAFT_DATA;
  let githubData = window.ALOGS_GITHUB_DATA;
  let githubError = '';
  let loadingContent = false;
  let contentError = '';
  let contentSignature = '';
  let watchTimer;
  const siteBase = new URL('./', location.href);
  const $ = (s) => document.querySelector(s);
  const escape = (s = '') => String(s).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const paths = {
    news:'<rect x="4" y="3" width="16" height="18" rx="1"/><path d="M8 7h8M8 11h8M8 15h5"/>',
    hot:'<path d="M12 3c1 4-5 5-5 10a5 5 0 0 0 10 0c0-3-2-5-2-5 0 3-3 3-3 3s2-4 0-8Z"/>',
    save:'<path d="M6 3h12v18l-6-4-6 4Z"/>',
    calendar:'<rect x="3" y="5" width="18" height="16" rx="2"/><path d="M8 3v4M16 3v4M3 11h18"/>',
    refresh:'<path d="M20 7v5h-5M4 17v-5h5"/><path d="M6 7a7 7 0 0 1 12-1l2 3M4 15l2 3a7 7 0 0 0 12-1"/>',
    search:'<circle cx="10.5" cy="10.5" r="6.5"/><path d="m16 16 4 4"/>',
    moon:'<path d="M20.8 13A9 9 0 0 1 11 3.2 9 9 0 1 0 20.8 13Z"/>',
    sun:'<circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M2 12h2M20 12h2M5 5l1.5 1.5M17.5 17.5 19 19M5 19l1.5-1.5M17.5 6.5 19 5"/>',
    info:'<circle cx="12" cy="12" r="9"/><path d="M12 11v6M12 7h.01"/>',
    external:'<path d="M14 3h7v7M21 3l-9 9M10 3H4a1 1 0 0 0-1 1v16a1 1 0 0 0 1 1h16a1 1 0 0 0 1-1v-6"/>',
    chevron:'<path d="m7 10 5 5 5-5"/>',
    star:'<path d="m12 3 2.8 5.7 6.2.9-4.5 4.4 1.1 6.2L12 17.3l-5.6 2.9 1.1-6.2L3 9.6l6.2-.9Z"/>',
    copy:'<rect x="8" y="8" width="13" height="13" rx="2"/><path d="M16 8V3H3v13h5"/>',
    check:'<path d="m5 12 4 4L19 6"/>',
    grid:'<rect x="3" y="3" width="7" height="7" rx="1"/><rect x="14" y="3" width="7" height="7" rx="1"/><rect x="3" y="14" width="7" height="7" rx="1"/><rect x="14" y="14" width="7" height="7" rx="1"/>',
    users:'<circle cx="9" cy="7" r="3"/><path d="M3 20v-2a6 6 0 0 1 12 0v2M17 4a3 3 0 0 1 0 6M21 20v-2a6 6 0 0 0-4-5.6"/>',
    mic:'<rect x="9" y="2" width="6" height="12" rx="3"/><path d="M5 10v2a7 7 0 0 0 14 0v-2M12 19v3M8 22h8"/>',
    skill:'<path d="m12 3 3 6 6 3-6 3-3 6-3-6-6-3 6-3Z"/>',
    list:'<path d="M8 6h13M8 12h13M8 18h13M3 6h.01M3 12h.01M3 18h.01"/>',
    arrowUp:'<path d="M12 20V4m-6 6 6-6 6 6"/>',
    close:'<path d="m6 6 12 12M6 18 18 6"/>'
  };
  const icon = name => `<svg class="icon" viewBox="0 0 24 24" aria-hidden="true">${paths[name] || paths.news}</svg>`;
  function hydrateIcons(root = document) { root.querySelectorAll('[data-icon]').forEach(e => e.innerHTML = icon(e.dataset.icon)); }
  const storage = {
    get(key, fallback) { try { return JSON.parse(localStorage.getItem(`alogs-ui-draft:${key}`)) ?? fallback; } catch { return fallback; } },
    set(key, value) { try { localStorage.setItem(`alogs-ui-draft:${key}`, JSON.stringify(value)); return true; } catch { return false; } }
  };
  const knownModules = ['news', 'builders', 'github'];
  const moduleViews = {news:['feed','hot','saved','topics','digest'],builders:['feed','podcasts','saved'],github:['feed','skills','saved']};
  const [initial, initialView] = (location.hash.slice(1)||window.ALOGS_INITIAL_ROUTE||'news').split('/');
  const requestedDay = new URLSearchParams(location.search).get('day');
  const state = { module: knownModules.includes(initial) ? initial : 'news', view:moduleViews[initial]?.includes(initialView)?initialView:'feed', day:requestedDay||D.meta.defaultDay, autoDay:!requestedDay, scope:'all', resourceScope:'recommended', category:'all', query:'', saved:new Set(storage.get('saved', [])) };
  const categories = {'ai-models':'模型','ai-products':'产品','industry':'行业','tip':'实践','paper':'研究','opinion':'评论'};
  const dayOf = value => value ? new Date(value).toLocaleDateString('sv-SE', {timeZone:'Asia/Shanghai'}) : '';
  const timeOf = value => value ? new Date(value).toLocaleTimeString('zh-CN', {timeZone:'Asia/Shanghai',hour:'2-digit',minute:'2-digit'}) : '';
  const dateOf = value => `${Number(dayOf(value).slice(5,7))}/${Number(dayOf(value).slice(8))}`;
  const dateMatch = value => state.day === 'all' || dayOf(value) === state.day;
  const searchMatch = (...values) => !state.query || values.join(' ').toLowerCase().includes(state.query.toLowerCase());
  const resourceMatch = r => state.day === 'all' || r.activities.some(a => a.day === state.day&&
    (a.recommended==null||Boolean(a.recommended)));
  const shownTweets = b => b.tweets.filter(t => state.view === 'saved' || dateMatch(t.createdAt));
  const shownDigest = b => {
    const date=state.day==='all'||state.view==='saved'?dayOf(shownTweets(b)[0]?.createdAt):state.day;
    return b.digests ? b.digests[date] : {title:b.title,summary:b.summary};
  };
  const installPrompt = r => r.installPrompt || (r.type==='skill'
    ? `帮我安装这个 Skill：${r.url}。请先阅读 SKILL.md，按官方说明安装。${r.installCommand?`\n使用命令：${r.installCommand}`:''}`
    : `帮我安装并运行这个项目：${r.url}。请先阅读 README，检查当前环境，并按项目官方说明完成安装和启动。`);
  const saveKey = (type, id) => `${type}:${id}`;
  const saveButton = (type, id, label) => {
    const key = saveKey(type, id), saved = state.saved.has(key);
    return `<button class="save-button" data-save="${escape(key)}" aria-pressed="${saved}" aria-label="${saved?'取消收藏':'收藏'}：${escape(label)}" title="${saved?'取消收藏':'收藏'}">${icon('save')}</button>`;
  };
  const external = (url, label, className = 'text-link') => `<a class="${className}" href="${escape(url)}" target="_blank" rel="noopener noreferrer">${escape(label)}${icon('external')}</a>`;
  let toastTimer;
  function toast(message) { clearTimeout(toastTimer); $('#toast').textContent=message; $('#toast').hidden=false; toastTimer=setTimeout(()=>$('#toast').hidden=true,2600); }
  function applyTheme(theme) {
    document.documentElement.dataset.theme=theme;
    $('#theme').innerHTML=icon(theme==='dark'?'sun':'moon');
    const label=theme==='dark'?'切换为浅色模式':'切换为深色模式';
    $('#theme').setAttribute('aria-label',label); $('#theme').title=label;
  }
  applyTheme(storage.get('theme', matchMedia('(prefers-color-scheme: dark)').matches?'dark':'light'));

  function newsItems(selectedOnly=false) {
    return D.news.filter(n=>(state.view==='saved'||dateMatch(n.publishedAt)) && (!selectedOnly||n.selected) &&
      (state.view!=='saved'||state.saved.has(saveKey('news',n.id))) &&
      (state.category==='all'||n.category===state.category) &&
      searchMatch(n.title,n.summary,n.source.name)).sort((a,b)=>b.publishedAt.localeCompare(a.publishedAt));
  }
  function builderItems() {
    return D.builders.filter(b => (state.view!=='saved'||state.saved.has(saveKey('builder',b.handle))) &&
      (state.category==='all'||state.category===b.handle) &&
      shownTweets(b).length>0 &&
      searchMatch(b.name,shownDigest(b)?.title,shownDigest(b)?.summary,shownTweets(b).map(t=>t.text).join(' ')));
  }
  const resourceStars = r => Number.isInteger(r.sourceStars)?r.sourceStars:
    Object.hasOwn(r,'sourceStars')?-1:Number.isInteger(r.originRepoStars)?r.originRepoStars:
    Number.isInteger(r.repoStars)?r.repoStars:-1;
  const resourceSource = r => r.sourceRepo||r.originRepo||r.repo||r.starRepo||'';
  const minimumStars = () => githubData?.recommendationPolicy?.minRepoStars ?? 1000;
  const resourceEligible = r => r.recommendation?.eligible === true && resourceStars(r)>=minimumStars();
  const resourceHistoryMatch = r => state.day==='all'||r.activities.some(a=>a.day===state.day)||
    (!r.activities.length&&r.discoveryDate===state.day);
  const resourceScopeMatch = r => state.resourceScope==='history'
    ? resourceHistoryMatch(r) : resourceEligible(r)&&resourceMatch(r);
  const historyReason = r => ({
    'below-min-stars':`低于 ${minimumStars().toLocaleString('en-US')} Star`,
    'archived-source':'来源已归档',
    'project-subdirectory':'教程或仓库子目录',
    'project-collection':'资源合集',
    'skill-collection':'纯 Skill 合集',
    'project-unverified':'独立软件尚未核实',
  }[r.recommendation?.reason]||'来源未核实')+' · 仅保留历史';
  const sourceLabel = source => source==='github-trending-weekly'?'GitHub Trending 周榜':source;
  function resourceItems() {
    return D.resources.filter(r=>(state.view==='saved'||resourceScopeMatch(r)) &&
      (state.view!=='saved'||state.saved.has(saveKey('resource',r.id))) &&
      (state.view==='saved'||r.type===(state.view==='skills'?'skill':'project')) &&
      searchMatch(r.name,r.repo,r.purpose,r.summary)).sort((a,b)=>
        resourceStars(b)-resourceStars(a)||(b.installs24h??-1)-(a.installs24h??-1)||
        a.publishedAt.localeCompare(b.publishedAt)||a.id.localeCompare(b.id));
  }
  function adoptGithub(data) {
    if (!data || data.schemaVersion!==3 || !Array.isArray(data.skills) || !Array.isArray(data.projects)
        || !Array.isArray(data.sources)) throw new Error('发布数据格式不完整');
    const resources=[...data.skills,...data.projects];
    if(resources.some(r=>!r.id||!r.url||!Array.isArray(r.activities))) throw new Error('资源记录不完整');
    D.resources=resources.map(r=>({...r, starRepo:resourceSource(r), sources:r.sources||[r.sourceName],
      purpose:r.purposeZh||r.titleZh||r.summary?.split(/(?<=[.!?。])\s/)[0]||r.name}));
    // Existing design-draft favorites retain the identity of their original resource.
    for(const old of legacyResources) {
      const updated=D.resources.find(r=>r.url===old.url);
      if(updated&&state.saved.has(saveKey('resource',old.id))) {
        state.saved.delete(saveKey('resource',old.id));state.saved.add(saveKey('resource',updated.id));
      }
    }
    storage.set('saved',[...state.saved]);
    githubData=data;githubError='';
    if(state.autoDay) state.day=latestDay();
  }
  function latestDay() {
    const days=[...D.news.map(n=>dayOf(n.publishedAt)),...D.builders.flatMap(b=>b.tweets.map(t=>dayOf(t.createdAt))),
      ...D.resources.flatMap(r=>r.activities.map(a=>a.day)),...(D.dailies||[]).map(r=>r.date),
      ...D.podcasts.map(p=>dayOf(p.publishedAt))].filter(Boolean);
    return days.sort().at(-1)||D.meta.defaultDay;
  }
  function renderDates() {
    const days=new Set([D.meta.defaultDay,dayOf(new Date().toISOString()),state.day,
      ...D.resources.flatMap(r=>r.activities.map(a=>a.day)),
      ...D.news.map(n=>dayOf(n.publishedAt)),...D.builders.flatMap(b=>b.tweets.map(t=>dayOf(t.createdAt))),
      ...D.podcasts.map(p=>dayOf(p.publishedAt)),...(D.dailies||[]).map(r=>r.date)]);
    days.delete('all');days.delete('');
    $('#global-date').innerHTML=[...days].sort().reverse().map(day=>
      `<option value="${escape(day)}">${Number(day.slice(5,7))} 月 ${Number(day.slice(8))} 日</option>`).join('')+'<option value="all">全部日期</option>';
  }
  function githubEmpty() {
    if(state.query) return empty('query');
    if(state.view==='saved') return empty('saved');
    const kind=state.view==='skills'?'skill':'project';
    const historyOnly=D.resources.filter(r=>r.type===kind&&resourceHistoryMatch(r)&&!resourceEligible(r)).length;
    if(state.resourceScope==='recommended'&&historyOnly) {
      return `<div class="empty-state">${icon('star')}<h2>暂无合格${kind==='skill'?' Skill':'项目'}推荐</h2><p>该日 ${historyOnly} 条旧记录不符合当前收录规则，仅保留历史。${kind==='project'?'项目须有独立源码仓库、可安装或运行。':''}真实来源至少 ${minimumStars().toLocaleString('en-US')} Star，不足 5 条时少推。</p><button class="resource-button" data-action="all-resources">查看全部历史</button></div>`;
    }
    const sources=githubData?.sources||[];
    const checked=sources.length>0&&sources.every(s=>s.last_success&&dayOf(s.last_success)===state.day);
    const failed=githubError||sources.some(s=>s.error&&(!s.last_success||dayOf(s.last_success)<=state.day));
    const title=failed?'同步未完成':checked?'已检查，暂无新发布':'这个日期尚未同步';
    const detail=failed?'保留已保存的内容，可点击顶部刷新重试。':checked?'当日没有可发布的新条目；待发布内容继续按每日额度顺延。':'没有该日检查记录，无法判断当天是否有更新。';
    return `<div class="empty-state">${icon('calendar')}<h2>${title}</h2><p>${detail}</p><button class="resource-button" data-action="all-resources">查看全部历史</button></div>`;
  }
  function navButton(view, label, symbol, count) {
    return `<button class="nav-item ${state.view===view?'active':''}" data-view="${view}" ${state.view===view?'aria-current="page"':''}>${icon(symbol)}${label}${count!==undefined?`<span class="nav-count">${count}</span>`:''}</button>`;
  }
  function renderNav() {
    let html;
    const savedCount = [...state.saved].filter(k=>k.startsWith(state.module==='news'?'news:':state.module==='builders'?'builder:':'resource:')).length;
    if (state.module==='news') html=`<div class="nav-group">${navButton('feed','动态','news')}${navButton('hot','热点','hot')}${navButton('saved','收藏','save',savedCount)}</div><details class="nav-more" ${['topics','digest'].includes(state.view)?'open':''}><summary>更多 ${icon('chevron')}</summary><div class="nav-group">${navButton('digest','当日提要','list')}${navButton('topics','主题','grid')}</div></details>`;
    if (state.module==='builders') html=`<div class="nav-group">${navButton('feed','观点','users')}${navButton('podcasts','播客','mic')}${navButton('saved','收藏','save',savedCount)}</div>`;
    if (state.module==='github') html=`<div class="nav-group">${navButton('feed','项目','grid',D.resources.filter(r=>r.type==='project'&&resourceScopeMatch(r)).length)}${navButton('skills','Skills','skill',D.resources.filter(r=>r.type==='skill'&&resourceScopeMatch(r)).length)}${navButton('saved','收藏','save',savedCount)}</div>`;
    $('#side-nav').innerHTML=html;
    document.querySelectorAll('[data-module]').forEach(b => {
      if(b.dataset.module===state.module) b.setAttribute('aria-current','page'); else b.removeAttribute('aria-current');
    });
  }
  function renderControls() {
    const isNews = state.module==='news' && state.view==='feed';
    $('#view-controls').innerHTML = isNews ? `<div class="segmented" aria-label="动态范围"><button data-scope="all" aria-pressed="${state.scope==='all'}">全部<small>${D.news.filter(n=>dateMatch(n.publishedAt)).length}</small></button><button data-scope="selected" aria-pressed="${state.scope==='selected'}">精选<small>${D.news.filter(n=>dateMatch(n.publishedAt)&&n.selected).length}</small></button></div>` :
      state.module==='github'?(state.view==='saved'?'<span class="sr-only">资源收藏</span>':`<div class="segmented" aria-label="资源范围"><button data-resource-scope="recommended" aria-pressed="${state.resourceScope==='recommended'}">推荐</button><button data-resource-scope="history" aria-pressed="${state.resourceScope==='history'}">历史</button></div>`):`<h1 class="view-heading">${state.view==='saved'?'收藏':state.view==='hot'?'热点':state.view==='topics'?'主题':state.view==='digest'?'当日提要':state.view==='podcasts'?'播客':'观点'}</h1>`;
    const searchable = !['topics','hot','digest','podcasts'].includes(state.view);
    $('.filter-controls').hidden=!searchable;
    const categoryVisible = searchable && state.module!=='github';
    $('.category-control').hidden=!categoryVisible;
    let options = '<option value="all">全部分类</option>';
    if(state.module==='builders') options='<option value="all">全部作者</option>'+D.builders.map(b=>`<option value="${escape(b.handle)}">${escape(b.name)}</option>`).join('');
    if(state.module==='news') options+=Object.entries(categories).map(([v,l])=>`<option value="${v}">${l}</option>`).join('');
    $('#category-filter').innerHTML=options; $('#category-filter').value=state.category;
    $('#category-filter').setAttribute('aria-label',state.module==='builders'?'筛选作者':'筛选分类');
    const placeholder=state.module==='news'?'搜索动态':state.module==='builders'?'搜索观点或作者':'搜索用途、项目或 Skill';
    $('#search').placeholder=placeholder; $('#search').setAttribute('aria-label',placeholder); $('#search').value=state.query;
    $('#global-date').value=state.day;
  }
  function noteCounts(count, label, note) {
    $('#result-count').innerHTML=`<strong>${count}</strong> ${label}`;
    const names=state.module==='builders'?['builders','summary']:state.view==='hot'?['hot']:state.view==='digest'?['daily']:['news'];
    const sources=names.map(n=>D.meta.sources?.[n]).filter(Boolean);
    const syncing=state.module!=='github'&&sources.some(s=>s.syncing||s.historySyncing);
    const failed=state.module!=='github'&&(contentError||sources.some(s=>s.error));
    $('#result-note').textContent=syncing?'正在更新 · 已有内容可继续阅读':failed?'更新未完成 · 保留已有内容':note;
    const status=$('.draft-status');
    if(status)status.innerHTML='<span></span>'+(syncing?'正在更新':contentError?'服务未连接':'内容已接入');
  }
  function empty(kind='query') {
    const info={
      date:['calendar','这个日期尚未同步','没有该日内容记录，无法判断当天是否有更新。','查看最近内容','snapshot'],
      saved:['save','把值得再看的内容留在这里','点击条目旁的收藏按钮，下次可以从这里继续阅读。','返回阅读','back'],
      query:['search','没有找到匹配的内容','试试更短的关键词，或清除筛选条件。','清除筛选','clear']
    }[kind];
    if(kind==='date') {
      const name=state.module==='builders'?'builders':state.view==='digest'?'daily':state.view==='hot'?'hot':'news';
      const source=D.meta.sources?.[name];
      if(source?.syncing||source?.historySyncing){info[1]='正在读取最新内容';info[2]='上游读取完成后会自动显示，请稍候。';}
      else if(source?.error){info[1]='该日内容读取未完成';info[2]='已有内容已保留，可点击顶部刷新重试。';}
      else if(source?.checkedDays?.includes(state.day)||source?.absentDays?.includes(state.day)){
        info[1]='已检查，暂无内容';info[2]='这个日期没有可显示的内容，可以切换日期继续阅读。';
      }
    }
    return `<div class="empty-state">${icon(info[0])}<h2>${info[1]}</h2><p>${info[2]}</p><button class="resource-button" data-action="${info[4]}">${info[3]}</button></div>`;
  }
  function newsRow(n, index) {
    const datePrefix=state.day==='all'||state.view==='saved'?`${dateOf(n.publishedAt)} `:'';
    return `<li><article class="news-row" data-record="${escape(n.id)}"><span class="item-seq" aria-hidden="true">${String(index+1).padStart(2,'0')}</span><div class="news-body">
      ${n.selected?`<span class="item-label">${icon('check')}精选</span>`:''}
      <h2><a href="${escape(n.links.original)}" target="_blank" rel="noopener noreferrer">${escape(n.title)}</a></h2>
      <p class="teaser">${escape(n.summary)}</p>
      <div class="item-bottom"><div class="news-tools">
        <div class="metadata"><span>${escape(n.source.name)}</span><span class="dot" aria-hidden="true">·</span><time datetime="${escape(n.publishedAt)}">${datePrefix}${timeOf(n.publishedAt)}</time><span class="dot" aria-hidden="true">·</span><span>${escape(categories[n.category]||n.category)}</span></div>
        <details class="article-details"><summary><span>展开摘要</span>${icon('chevron')}</summary><div class="expanded-copy"><p>${escape(n.summary)}</p><p class="original-title">原标题：${escape(n.originalTitle)}</p>${n.reason?`<p class="reason">精选理由：${escape(n.reason)}</p>`:''}</div></details>
      </div><div class="row-actions">${external(n.links.original,'读原文')}${saveButton('news',n.id,n.title)}</div></div>
    </div></article></li>`;
  }
  function builderRow(b) {
    const tweets=shownTweets(b);
    const digest=shownDigest(b);
    const latest=state.day==='all'||state.view==='saved';
    return `<li><article class="opinion-row"><div class="author-info"><a class="author-name" href="https://x.com/${escape(b.handle)}" target="_blank" rel="noopener noreferrer">${escape(b.name)}</a><span class="author-handle">@${escape(b.handle)}</span><p class="author-role">${escape(b.role)}</p></div>
      <div class="opinion-body">${digest?`<h2>${escape(digest.title)}</h2><p>${escape(digest.summary)}</p>${latest?`<p class="digest-date">${dateOf(tweets[0]?.createdAt)} 提要 · 下方保留全部原帖</p>`:''}`:`<h2>${escape(b.name)} 的最新原帖</h2><p class="original-preview" lang="en">${escape(tweets[0]?.text)}</p>`}
      <div class="opinion-bottom"><details class="article-details"><summary>查看 ${tweets.length} 条原帖${icon('chevron')}</summary><div class="original-posts">${tweets.map(t=>`<div class="original-post"><p lang="en">${escape(t.text)}</p><div class="metadata"><time datetime="${escape(t.createdAt)}">${dateOf(t.createdAt)} ${timeOf(t.createdAt)}</time><span>赞 ${t.likes}</span>${external(t.url,'在 X 阅读')}</div></div>`).join('')}</div></details><span class="metadata">${timeOf(tweets[0]?.createdAt)}</span>${saveButton('builder',b.handle,b.name+'的观点')}</div></div>
    </article></li>`;
  }
  function resourceRow(r, pending=false) {
    const stars=resourceStars(r), source=resourceSource(r);
    const sourceUrl=r.sourceUrl||r.originUrl||r.url;
    const prompt=installPrompt(r);
    const owner=source.split('/')[0];
    return `<article class="resource" data-record="${escape(r.id)}"><div class="resource-top"><a class="repo-name" href="${escape(sourceUrl)}" target="_blank" rel="noopener noreferrer">${escape(r.name)}<span>${escape(r.type==='skill'?source:owner)}</span></a>
      ${stars>=0?`<a class="star" href="${escape(`https://github.com/${source}`)}" target="_blank" rel="noopener noreferrer" title="${escape(source)} 来源仓库 Star · ${escape(r.sourceMetricsAt||r.originMetricsAt||r.metricsAt||'已保存快照')}${r.sourceStatus==='stale'?' · 本次核对失败，保留上次指标':''}">${icon('star')}<span>${stars.toLocaleString('en-US')}</span><small>Star</small></a>`:''}</div>
      <h2>${escape(r.purpose)}</h2>
      ${!pending&&!resourceEligible(r)?`<p class="history-note">${escape(historyReason(r))}</p>`:''}
      <div class="resource-metrics">${r.weeklyRankingUrl?`<a class="text-link" href="${escape(r.weeklyRankingUrl)}" target="_blank" rel="noopener noreferrer" title="${escape(r.repo)} 仓库上榜 · ${escape(r.weeklyRankingAt)}">Trending 周榜${r.type==='project'&&Number.isInteger(r.weeklyStars)?` · 本周 +${r.weeklyStars.toLocaleString('en-US')} Star`:''}</a>`:''}${Number.isInteger(r.installs24h)?`<a class="text-link" href="${escape(r.rankingUrl)}" target="_blank" rel="noopener noreferrer" title="skills.sh · ${escape(r.rankingAt)}">24 小时安装 <strong>${r.installs24h.toLocaleString('en-US')}</strong></a>`:''}${pending?'<span>待发布</span>':`<time datetime="${escape(r.publishedAt)}">${dateOf(r.publishedAt)} 收录${r.activities.some(a=>a.day===state.day&&a.kind==='updated')?' · 当日更新':''}</time>`}</div>
      <div class="resource-footer">${external(sourceUrl,r.type==='skill'?'查看 Skill':'查看项目','resource-button')}<button class="resource-button copy-button" data-copy="${escape(r.id)}">${icon('copy')}复制安装</button>${pending?'':saveButton('resource',r.id,r.name)}</div>
      <details class="article-details"><summary>${prompt?'安装话术与来源':'来源与使用说明'}${icon('chevron')}</summary><div class="expanded-copy">
        <p>收录来源：${escape(r.sources.map(sourceLabel).join('、'))}</p><p>Star 来源：${external(`https://github.com/${source}`,source)}${stars>=0?` · ${stars.toLocaleString('en-US')} Star（整个来源仓库）`:''}</p>${r.weeklyRankingUrl?`<p>${external(r.weeklyRankingUrl,'GitHub Trending 周榜')} · ${escape(r.repo)} 仓库上榜；收录日期不代表仓库创建日期。</p>`:''}${r.originUrl?`<p>原作者来源：${external(r.originUrl,r.originRepo)}</p><p>本页收录的是该来源的改编版本；${external(r.originFileUrl||r.originUrl,'阅读原始技能')}</p>`:''}${r.sourceError?`<p>${r.sourceStatus==='stale'?'本次来源核对失败，保留上次指标。':'来源尚未核实，未用于 Star 择优。'}</p>`:''}<p>${escape(r.summary)}</p><p>${r.purposeZh?'用途依据原文整理；':'用途保留来源原文；'}${external(r.readme||r.fileUrl||r.url,'阅读收录版本')}</p>
        <label>安装话术<textarea class="install-prompt" readonly aria-label="${escape(r.name)} 的安装话术">${escape(prompt)}</textarea></label><p>复制后粘贴到 AI 工具中，具体安装方法以项目 README 为准。</p>
      </div></details></article>`;
  }
  function renderHot() {
    const unique=new Map();
    D.hot.filter(h=>dateMatch(h.latestAt)).sort((a,b)=>b.latestAt.localeCompare(a.latestAt)).forEach(h=>{if(!unique.has(h.id))unique.set(h.id,h);});
    const items=[...unique.values()].sort((a,b)=>(a.rank||99)-(b.rank||99));
    noteCounts(items.length,'个热点','按多来源聚合');
    return items.length?items.map(h=>{
      const links=h.links||{};
      const primary=D.news.find(n=>n.id===h.id);
      return `<article class="hot-row"><h2>${escape(h.title)}</h2><div class="metadata"><span>${h.sourceCount} 个来源</span><time datetime="${escape(h.latestAt)}">${dateOf(h.latestAt)} ${timeOf(h.latestAt)}</time></div>${primary?`<p>${escape(primary.summary)}</p>`:''}<div class="metadata hot-sources">${escape(h.sourceNames.join(' · '))}</div><div class="source-links">${links.story?external(links.story,'阅读事件聚合'):''}${links.original?external(links.original,h.source?.name||'读原文'):primary?external(primary.links.original,primary.source.name):''}</div></article>`;
    }).join(''):empty('date');
  }
  function renderPodcast() {
    const items=D.podcasts.filter(p=>dateMatch(p.publishedAt)).sort((a,b)=>b.publishedAt.localeCompare(a.publishedAt));
    noteCounts(items.length,'期播客','发布日期以原始来源为准');
    return items.length?items.map(p=>`<article class="podcast"><div class="metadata">${escape(p.name)} · ${dateOf(p.publishedAt)}</div><h2>${escape(p.titleZh||p.title)}</h2>${p.summary?`<p>${escape(p.summary)}</p>`:''}<ol>${p.points.map(x=>`<li>${escape(x)}</li>`).join('')}</ol>${external(p.url,'打开节目来源','resource-button')}${p.transcript?`<details class="article-details"><summary>展开逐字稿${icon('chevron')}</summary><div class="original-post transcript"><p lang="en">${escape(p.transcript)}</p></div></details>`:''}</article>`).join(''):empty('date');
  }
  function renderDaily() {
    const items=(D.dailies||[]).filter(r=>state.day==='all'||r.date===state.day).sort((a,b)=>b.date.localeCompare(a.date));
    noteCounts(items.length,'期日报','由 AIHOT 整理 · 统计区间见日报');
    return items.length?items.map(r=>`<article class="daily-report"><div class="daily-lead"><h2>${escape(r.lead.title)}</h2><p>${escape(r.lead.leadParagraph)}</p><div class="metadata">${escape(r.date)} 日报 · 统计 ${dateOf(r.windowStart)} ${timeOf(r.windowStart)} — ${dateOf(r.windowEnd)} ${timeOf(r.windowEnd)}（北京时间）${external(r.links.aihot,'阅读完整日报')}</div></div>${r.sections.map(s=>`<section class="daily-section"><h3>${escape(s.label)}</h3>${s.items.map(n=>`<article class="daily-item"><h2>${external(n.links.original,n.title,'daily-title')}</h2><p>${escape(n.summary)}</p><div class="metadata">${escape(n.source.name)}</div></article>`).join('')}</section>`).join('')}</article>`).join(''):empty('date');
  }
  function renderContent() {
    let html='',count=0;
    $('#content').setAttribute('aria-label',state.view==='saved'?'收藏内容':state.module==='news'?'动态内容':state.module==='builders'?'创作者观点':'GitHub 资源');
    if(state.view==='hot') html=renderHot();
    else if(state.view==='podcasts') html=renderPodcast();
    else if(state.view==='digest') html=renderDaily();
    else if(state.view==='topics') {
      const entries=Object.entries(categories).map(([k,l])=>({key:k,label:l,count:D.news.filter(n=>dateMatch(n.publishedAt)&&n.category===k).length})).filter(x=>x.count);
      noteCounts(entries.length,'个主题','按内容分类阅读');
      html=entries.length?`<div class="topic-grid">${entries.map(t=>`<button class="topic" data-topic="${t.key}">${t.label}<span>${t.count} 条 ${icon('external')}</span></button>`).join('')}</div>`:empty('date');
    } else if(state.module==='news') {
      const items=newsItems(state.scope==='selected'); count=items.length;
      noteCounts(count,'条动态',state.view==='saved'?'跨日期保存':state.scope==='selected'?'AIHOT 精选':'按发布时间排序');
      html=items.length?`<ol class="news-list">${items.map(newsRow).join('')}</ol>`:empty(state.view==='saved'&&!state.query&&state.category==='all'?'saved':state.query||state.category!=='all'?'query':'date');
    } else if(state.module==='builders') {
      const items=builderItems(); count=items.length; const posts=items.reduce((n,b)=>n+shownTweets(b).length,0);
      noteCounts(count,`位作者 · ${posts} 条原帖`,state.view==='saved'?'跨日期保存':'中文提要 · 原帖可展开');
      html=count?`<ol class="opinion-list">${items.map(builderRow).join('')}</ol>`:empty(state.view==='saved'&&!state.query&&state.category==='all'?'saved':state.query||state.category!=='all'?'query':'date');
    } else {
      const items=resourceItems(); count=items.length;
      const pending=githubData?.dailyStatus?.pending?.[state.view==='skills'?'skill':'project']||0;
      const note=githubError?'读取失败，保留已有内容':state.view==='saved'?'跨日期保存':state.resourceScope==='history'?'已发布历史 · 含未达门槛的旧条目':`真实来源 ≥${minimumStars().toLocaleString('en-US')} Star · 每日最多 5 条${pending?` · ${pending} 条待发布`:''}`;
      noteCounts(count,state.view==='saved'?'项资源':state.view==='skills'?'个 Skills':'个项目',note);
      html=count?`<div class="resources-grid">${items.map(r=>resourceRow(r)).join('')}</div>`:githubEmpty();
      const waiting=state.resourceScope==='history'?null:(state.view==='skills'?githubData?.pendingSkills:state.view==='feed'?githubData?.pendingProjects:null)?.filter(resourceEligible);
      if(waiting?.length) {
        html+=`<details class="pending-skills article-details"><summary>待发布${state.view==='skills'?' Skills':'项目'} · 来源 Star 优先${icon('chevron')}</summary><p class="pending-note">预览排序靠前的 ${waiting.length} 条合格候选，按后续每日额度顺延；不足 5 条不补满。</p><div class="resources-grid">${waiting.map(r=>resourceRow({...r, starRepo:resourceSource(r), sources:r.sources||[r.sourceName],
          purpose:r.purposeZh||r.summary?.split(/(?<=[.!?。])\s/)[0]||r.name},true)).join('')}</div></details>`;
      }
    }
    $('#content').innerHTML=html;
  }
  function renderAll() { renderDates(); renderNav(); renderControls(); renderContent(); }
  function setView(view) {
    state.view=moduleViews[state.module].includes(view)?view:'feed'; state.scope='all'; state.category='all'; state.query='';
    history.replaceState(null,'',location.pathname+location.search+`#${state.module}${view!=='feed'?'/'+view:''}`);
    renderAll(); window.scrollTo({top:0,behavior:'instant'}); if(state.view==='digest')loadDaily();
  }
  async function copyInstall(id, button) {
    const r=[...D.resources,...(githubData?.pendingSkills||[]),...(githubData?.pendingProjects||[])].find(x=>x.id===id); if(!r) return;
    const prompt=installPrompt(r);
    let copied=false;
    try { if(navigator.clipboard && window.isSecureContext) { await navigator.clipboard.writeText(prompt); copied=true; } } catch {}
    if(!copied) {
      const details=button.closest('.resource').querySelector('details'); details.open=true;
      const text=details.querySelector('textarea'); text.focus(); text.select();
      try { copied=document.execCommand('copy'); } catch {}
    }
    if(copied) {
      button.innerHTML=icon('check')+'已复制'; button.classList.add('copied'); toast('安装话术已复制');
      setTimeout(()=>{if(button.isConnected){button.innerHTML=icon('copy')+'复制安装';button.classList.remove('copied');}},2400);
    } else toast('话术已选中，请手动复制');
  }
  document.addEventListener('click',e=>{
    const module=e.target.closest('[data-module]');
    if(module) { state.module=module.dataset.module; location.hash=state.module; setView('feed'); return; }
    const nav=e.target.closest('[data-view]');
    if(nav) { setView(nav.dataset.view); return; }
    const scope=e.target.closest('[data-scope]');
    if(scope) { state.scope=scope.dataset.scope; renderControls(); renderContent(); return; }
    const save=e.target.closest('[data-save]');
    if(save) {
      const key=save.dataset.save, was=state.saved.has(key);
      if(was) state.saved.delete(key); else state.saved.add(key);
      const persisted=storage.set('saved',[...state.saved]);
      save.setAttribute('aria-pressed',String(!was));
      const label=save.getAttribute('aria-label').replace(/^(取消收藏|收藏)：/,'');
      save.setAttribute('aria-label',`${was?'收藏':'取消收藏'}：${label}`); save.title=was?'收藏':'取消收藏';
      renderNav(); if(state.view==='saved'&&was) renderContent();
      toast(persisted?(was?'已取消收藏':'已收藏'):'已保存到本次页面，浏览器未允许持久保存'); return;
    }
    const copy=e.target.closest('[data-copy]');
    if(copy) { copyInstall(copy.dataset.copy,copy); return; }
    const topic=e.target.closest('[data-topic]');
    if(topic) { state.view='feed'; state.category=topic.dataset.topic; state.query=''; renderAll(); return; }
    const action=e.target.closest('[data-action]')?.dataset.action;
    if(action==='about') $('#about-dialog').showModal();
    if(action==='close-dialog') $('#about-dialog').close();
    if(action==='snapshot') {state.day=latestDay();state.autoDay=true;renderAll();}
    if(action==='back') setView('feed');
    if(action==='clear') {state.query='';state.category='all';state.scope='all';renderAll();$('#search').focus();}
    if(action==='all-resources') {state.resourceScope='history';state.day='all';state.autoDay=false;renderAll();}
    const resourceScope=e.target.closest('[data-resource-scope]')?.dataset.resourceScope;
    if(resourceScope) {state.resourceScope=resourceScope;renderAll();}
  });
  $('#global-date').addEventListener('change',e=>{
    state.day=e.target.value;state.autoDay=false;
    const url=new URL(location.href);url.searchParams.set('day',state.day);history.replaceState(null,'',url);
    renderAll(); if(state.view==='digest')loadDaily();
  });
  $('#category-filter').addEventListener('change',e=>{ state.category=e.target.value; renderContent(); });
  $('#search').addEventListener('input',e=>{state.query=e.target.value.trim();renderContent();});
  $('#theme').addEventListener('click',()=>{const theme=document.documentElement.dataset.theme==='dark'?'light':'dark';applyTheme(theme);storage.set('theme',theme);});
  function adoptContent(data) {
    if(!data?.meta||!['news','builders','hot','podcasts','dailies'].every(k=>Array.isArray(data[k])))throw new Error('内容数据不完整');
    ['meta','news','builders','hot','podcasts','dailies'].forEach(k=>D[k]=data[k]);
    contentError='';
    if(state.autoDay)state.day=latestDay();
  }
  async function api(path, options={}) {
    const controller=new AbortController(),timer=setTimeout(()=>controller.abort(),12000);
    try {
      const response=await fetch(new URL(path,siteBase),{cache:'no-store',signal:controller.signal,...options});
      if(!response.ok)throw new Error(`HTTP ${response.status}`);
      return await response.json();
    } finally {clearTimeout(timer);}
  }
  async function refreshContent(manual=false) {
    if(loadingContent)return;
    loadingContent=true;
    $('#refresh').disabled=true;$('#refresh').setAttribute('aria-busy','true');
    try {
      const manifest=await api('data/manifest.json?check='+Date.now());
      const signature=manifest.version;
      if(signature!==contentSignature||manual||contentError){
        const content=await api(manifest.content);
        const next=await api(manifest.github);
        adoptContent(content);adoptGithub(next);contentSignature=signature;renderAll();
      }
      contentError='';githubError='';
      clearTimeout(watchTimer);
      if(manual)toast('已刷新');
    } catch(error) {
      contentError='更新读取失败';
      renderAll();
      if(manual)toast('网络读取失败，已保留当前内容');
    } finally {loadingContent=false;$('#refresh').disabled=false;$('#refresh').removeAttribute('aria-busy');}
  }
  async function loadDaily() {
    // Daily history is collected in the cloud; reading never starts a model call.
    return;
  }
  $('#refresh').addEventListener('click',()=>refreshContent(true));
  $('#about-dialog').addEventListener('click',e=>{if(e.target===$('#about-dialog')){const r=e.target.getBoundingClientRect();if(e.clientX<r.left||e.clientX>r.right||e.clientY<r.top||e.clientY>r.bottom)e.target.close();}});
  document.addEventListener('keydown',e=>{
    if(e.key==='/'&&!e.metaKey&&!e.ctrlKey&&!e.altKey&&!['INPUT','TEXTAREA','SELECT'].includes(e.target.tagName)&&!$('.filter-controls').hidden&&!$('#about-dialog').open) {e.preventDefault();$('#search').focus();}
  });
  window.addEventListener('hashchange',()=>{
    const [module,view]=location.hash.slice(1).split('/');
    const nextView=moduleViews[module]?.includes(view)?view:'feed';
    if(knownModules.includes(module)&&(module!==state.module||nextView!==state.view)){state.module=module;setView(nextView);}
  });
  window.addEventListener('storage',e=>{if(e.key==='alogs-ui-draft:saved'){state.saved=new Set(storage.get('saved',[]));renderNav();renderContent();}});
  D.resources=[];
  if(githubData){try{adoptGithub(githubData);}catch(error){githubError=error.message;}}
  hydrateIcons(); renderAll();refreshContent();if(state.view==='digest')loadDaily();
  document.addEventListener('visibilitychange',()=>{if(!document.hidden)refreshContent();});
  setInterval(()=>{if(!document.hidden)refreshContent();},60000);
})();
