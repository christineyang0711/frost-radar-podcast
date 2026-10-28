"""Cloud queue release: Python standard library only; no TTS or LLM credentials."""
import argparse
from datetime import date, datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
from zoneinfo import ZoneInfo

from podcast_feed import atomic_write, write_feed


def read_entries(site, folder):
    entries=[]
    for path in sorted((Path(site)/folder).glob('*.json')):
        if not re.fullmatch(r'\d{4}-\d{2}-\d{2}\.json',path.name):raise ValueError('invalid episode filename')
        e=json.loads(path.read_text());date.fromisoformat(e['date']);date.fromisoformat(e['source_day'])
        if e.get('report_date'):date.fromisoformat(e['report_date'])
        if path.stem!=e['date'] or not e.get('guid'):raise ValueError('invalid episode identity')
        audio=Path(site)/'episodes'/(e['date']+'.mp3')
        if not audio.is_file() or audio.is_symlink():raise ValueError('missing episode audio: '+e['date'])
        if audio.stat().st_size!=e['bytes']:raise ValueError('episode byte count changed: '+e['date'])
        if hashlib.sha256(audio.read_bytes()).hexdigest()!=e['audio_sha256']:raise ValueError('episode hash changed: '+e['date'])
        if folder=='published':
            stamp=datetime.fromisoformat(e['published_at'])
            if stamp.tzinfo is None:raise ValueError('publication timezone required')
        entries.append(e)
    return entries


def catalog(site):
    published=read_entries(site,'published');staged=read_entries(site,'staged')
    entries=published+staged
    for key in ['date','guid','source_day']:
        values=[e[key] for e in entries]
        if len(values)!=len(set(values)):raise ValueError('duplicate '+key+' across queue/catalog')
    days=[e['release_day'] for e in published]
    if len(days)!=len(set(days)):raise ValueError('more than one episode released on a Shanghai date')
    return published,staged


def release_one(site, now=None, dry_run=False):
    site=Path(site);config=json.loads((site/'channel.json').read_text())
    clock=now or datetime.now(timezone.utc)
    if clock.tzinfo is None:raise ValueError('release clock must be timezone aware')
    today=clock.astimezone(ZoneInfo('Asia/Shanghai')).date().isoformat()
    # Recover a crash between writing published metadata and removing queue entry.
    # Only remove a duplicate if the immutable identity is exactly the same.
    for path in (site/'staged').glob('*.json'):
        target=site/'published'/path.name
        if target.exists():
            a=json.loads(path.read_text());b=json.loads(target.read_text())
            if any(a.get(k)!=b.get(k) for k in ('date','guid','source_day','audio_sha256')):
                raise ValueError('conflicting partial release')
            if not dry_run:path.unlink()
    published,staged=catalog(site)
    status='already_released' if any(e['release_day']==today for e in published) else 'queue_empty'
    selected=None
    if status!='already_released':
        eligible=[e for e in staged if e['source_day']<=today]
        if eligible:
            selected=max(eligible,key=lambda e:(e.get('report_date',e['source_day']),e['source_day'],e['date']))
            status='would_release' if dry_run else 'released'
            if not dry_run:
                e=dict(selected,published_at=clock.astimezone(timezone.utc).isoformat(),release_day=today,
                       display_date=today,status='published')
                atomic_write(site/'published'/(e['date']+'.json'),json.dumps(e,ensure_ascii=False,indent=2))
                (site/'staged'/(e['date']+'.json')).unlink()
                published.append(e);staged=[s for s in staged if s['date']!=e['date']]
    if not dry_run:
        if not published:raise ValueError('no published episodes available for feed')
        write_feed(published,site,config)
    return dict(status=status,release_day=today,selected=selected['date'] if selected else None,
                published=len(published),staged=len(staged))


def export_site(site, destination):
    site=Path(site);destination=Path(destination)
    if destination.resolve()==site.resolve() or site.resolve() in destination.resolve().parents:
        raise ValueError('export must be outside the Git checkout')
    if destination.exists():raise ValueError('export directory already exists')
    published,_=catalog(site);destination.mkdir(parents=True)
    for name in ['feed.xml','index.html','cover.png','.nojekyll']:
        source=site/name
        if source.is_symlink() or not source.is_file():raise ValueError('invalid public artifact')
        shutil.copy2(source,destination/name)
    (destination/'episodes').mkdir()
    for e in published:shutil.copy2(site/'episodes'/(e['date']+'.mp3'),destination/'episodes'/(e['date']+'.mp3'))
    return len(published)


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--root',type=Path,default=Path(__file__).resolve().parent)
    p.add_argument('--dry-run',action='store_true');p.add_argument('--export-dir',type=Path);p.add_argument('--verify-only',action='store_true')
    args=p.parse_args()
    if args.verify_only:
        pub,queue=catalog(args.root);result={'published':len(pub),'staged':len(queue)}
    else:result=release_one(args.root,dry_run=args.dry_run)
    if args.export_dir:result['exported']=export_site(args.root,args.export_dir)
    print(json.dumps(result,ensure_ascii=False))
    if os.environ.get('GITHUB_STEP_SUMMARY'):
        with open(os.environ['GITHUB_STEP_SUMMARY'],'a',encoding='utf-8') as summary:
            summary.write('## Podcast release\n\n'+json.dumps(result,ensure_ascii=False)+'\n')
    if not args.dry_run and result.get('status')=='queue_empty':raise SystemExit(2)
