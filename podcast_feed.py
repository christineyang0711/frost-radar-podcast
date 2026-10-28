"""RSS 2.0 / Apple Podcasts feed, exact KPI shownotes and stable episode IDs."""
from datetime import datetime
from email.utils import format_datetime
import html
import os
import tempfile
from pathlib import Path
import re
from urllib.parse import urlsplit
import xml.etree.ElementTree as ET

ITUNES='http://www.itunes.com/dtds/podcast-1.0.dtd'
CONTENT='http://purl.org/rss/1.0/modules/content/'
ATOM='http://www.w3.org/2005/Atom'
for prefix,ns in [('itunes',ITUNES),('content',CONTENT),('atom',ATOM)]:ET.register_namespace(prefix,ns)


def atomic_write(path, text):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    with tempfile.NamedTemporaryFile('w',encoding='utf-8',dir=path.parent,delete=False) as f:
        temp=Path(f.name)
        try:
            f.write(text);f.flush();os.fsync(f.fileno())
        except BaseException:
            temp.unlink(missing_ok=True);raise
    try:os.replace(temp,path)
    finally:temp.unlink(missing_ok=True)


def site_base(value):
    u=urlsplit(value)
    if u.scheme!='https' or not u.hostname or u.username or u.password or u.query or u.fragment:
        raise ValueError('site_url must be a public HTTPS base URL')
    return value.rstrip('/')+'/'


def shownotes(payload):
    a=payload['report']['analysis'];parts=['<h2>报告白话导读</h2>']
    for k,title in [('background','是什么'),('change','为什么现在看'),('takeaway','能收获什么')]:
        if k in a.get('executive_summary',{}):
            parts.append('<p><strong>'+title+'：</strong>'+html.escape(a['executive_summary'][k]['text'])+'</p>')
    parts.append('<h2>核心 KPI 数据摘要</h2><ul>')
    for k in a['market']['kpis']:
        pages=sorted({e['page'] for e in k['evidence']})
        refs='（物理页 '+ '、'.join(map(str,pages))+'）' if pages else ''
        parts.append('<li>'+html.escape(k['label']+'：'+k['value']+refs)+'</li>')
    parts.append('</ul>')
    source=payload['report'].get('source_url','')
    if urlsplit(source).scheme=='https':parts.append('<p><a href="'+html.escape(source,quote=True)+'">报告官网来源</a></p>')
    parts.append('<p>晓亮与苏苏为 AI 合成声音。商业推断与报告事实在节目中区分说明。</p>')
    return ''.join(parts)


def write_feed(episodes, site, config):
    site=Path(site);base=site_base(config['site_url'])
    root=ET.Element('rss',version='2.0');channel=ET.SubElement(root,'channel')
    def put(parent,tag,text):ET.SubElement(parent,tag).text=str(text)
    title=config.get('title','商业雷达 · 通勤播客')
    put(channel,'title',title);put(channel,'link',base);put(channel,'language','zh-cn')
    put(channel,'description','晓亮与苏苏用双人对谈拆解每日白皮书：市场、壁垒、痛点和切入机会。')
    put(channel,f'{{{ITUNES}}}author',config.get('author','Christine'))
    put(channel,f'{{{ITUNES}}}explicit','false');put(channel,f'{{{ITUNES}}}type','episodic')
    put(channel,f'{{{ITUNES}}}summary','每日白皮书商业拆解，由 AI 合成声音呈现。精确数字见每集简介。')
    category=ET.SubElement(channel,f'{{{ITUNES}}}category',text='Business')
    ET.SubElement(category,f'{{{ITUNES}}}category',text='Entrepreneurship')
    ET.SubElement(channel,f'{{{ITUNES}}}image',href=base+'cover.png')
    ET.SubElement(channel,f'{{{ATOM}}}link',href=base+'feed.xml',rel='self',type='application/rss+xml')
    def publication_order(episode):
        stamp=datetime.fromisoformat(episode['published_at'])
        if stamp.tzinfo is None:raise ValueError('episode time must include timezone')
        return stamp,episode['date']
    ordered=sorted(episodes,key=publication_order,reverse=True)
    seen=set()
    for episode in ordered:
        day=episode['date']
        if not re.fullmatch(r'\d{4}-\d{2}-\d{2}',day) or day in seen:raise ValueError('duplicate or invalid episode date')
        seen.add(day);audio=site/'episodes'/(day+'.mp3')
        if not audio.is_file() or audio.is_symlink() or audio.stat().st_size<1:raise ValueError('enclosure audio missing')
        item=ET.SubElement(channel,'item');put(item,'title',episode.get('display_date',day)+' · '+episode['title'])
        put(item,'link',base+'#episode-'+day)
        guid=ET.SubElement(item,'guid',isPermaLink='false');guid.text=episode['guid']
        stamp=datetime.fromisoformat(episode['published_at'])
        if stamp.tzinfo is None:raise ValueError('episode time must include timezone')
        put(item,'pubDate',format_datetime(stamp))
        put(item,'description',episode['shownotes']);put(item,f'{{{CONTENT}}}encoded',episode['shownotes'])
        plain=html.unescape(re.sub('<[^>]+>',' ',episode['shownotes']))
        put(item,f'{{{ITUNES}}}summary',plain)
        put(item,f'{{{ITUNES}}}duration',int(round(episode['duration'])))
        put(item,f'{{{ITUNES}}}explicit','false');put(item,f'{{{ITUNES}}}episodeType','full')
        ET.SubElement(item,'enclosure',url=base+'episodes/'+day+'.mp3',length=str(audio.stat().st_size),type='audio/mpeg')
    if not episodes:raise ValueError('cannot publish empty feed')
    xml=ET.tostring(root,encoding='utf-8',xml_declaration=True).decode()
    ET.fromstring(xml);atomic_write(site/'feed.xml',xml)
    entries=''.join('<article id="episode-'+e['date']+'"><h2>'+html.escape(e.get('display_date',e['date'])+' · '+e['title'])+'</h2><audio controls preload="none" src="episodes/'+e['date']+'.mp3"></audio>'+e['shownotes']+'</article>' for e in ordered)
    atomic_write(site/'index.html','<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>'+html.escape(title)+'</title><style>body{max-width:780px;margin:40px auto;padding:20px;background:#f5f4ef;color:#263a38;font:18px/1.7 system-ui}article{background:white;padding:24px;margin:24px 0;border-radius:20px}audio{width:100%}a{color:#397b6c}</style><h1>'+html.escape(title)+'</h1><p>在 Apple 播客中，通过 URL 关注：<a href="feed.xml">'+html.escape(base+'feed.xml')+'</a></p>'+entries+'</html>')
    atomic_write(site/'.nojekyll','')
    return site/'feed.xml'
