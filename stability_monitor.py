"""Histórico de estabilidade do Digital Signage: SQLite, resumo noturno e retenção."""
from __future__ import annotations
import json
import math
import time
from datetime import datetime, date, timedelta
from zoneinfo import ZoneInfo

TZ = ZoneInfo("America/Sao_Paulo")
SAMPLE_SECONDS = 300
EVENT_RETENTION_DAYS = 365
SAMPLE_RETENTION_DAYS = 90
_last_cleanup = 0.0
ALLOWED_EVENTS = {"monitor_started", "system_reboot", "player_restart", "browser_start", "browser_restart"}

def init_schema(conn):
    conn.executescript("""
      CREATE TABLE IF NOT EXISTS stability_events(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        tv_id TEXT NOT NULL,
        event_uid TEXT NOT NULL,
        occurred_at REAL NOT NULL,
        received_at REAL NOT NULL,
        event_type TEXT NOT NULL,
        severity TEXT NOT NULL,
        reason TEXT NOT NULL DEFAULT '',
        content_name TEXT NOT NULL DEFAULT '',
        UNIQUE(tv_id, event_uid)
      );
      CREATE INDEX IF NOT EXISTS idx_stability_event_tv_time ON stability_events(tv_id,occurred_at DESC);
      CREATE TABLE IF NOT EXISTS stability_samples(
        tv_id TEXT NOT NULL,
        bucket INTEGER NOT NULL,
        observed_at REAL NOT NULL,
        boot_id TEXT,
        cpu_percent REAL,
        memory_percent REAL,
        temperature_c REAL,
        disk_free_mb REAL,
        playback_state TEXT,
        sync_status TEXT,
        browser_running INTEGER,
        PRIMARY KEY(tv_id,bucket)
      );
      CREATE INDEX IF NOT EXISTS idx_stability_sample_tv_time ON stability_samples(tv_id,observed_at);
    """)
    conn.commit()

def _number(value, low=None, high=None):
    try:
        val=float(value)
        if not math.isfinite(val) or (low is not None and val<low) or (high is not None and val>high):return None
        return round(val,2)
    except (TypeError,ValueError,OverflowError): return None

def ingest(conn, tv_id, payload, received_at):
    """Idempotente: devolve IDs aceitos (inclusive os já existentes)."""
    global _last_cleanup
    bucket=int(received_at // SAMPLE_SECONDS)
    conn.execute("""INSERT INTO stability_samples
      (tv_id,bucket,observed_at,boot_id,cpu_percent,memory_percent,temperature_c,disk_free_mb,playback_state,sync_status,browser_running)
      VALUES(?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(tv_id,bucket) DO NOTHING""",
      (tv_id,bucket,received_at,str(payload.get('stability_boot_id') or '')[:80],
       _number(payload.get('cpu_percent'),0,100),_number(payload.get('memory_percent'),0,100),
       _number(payload.get('temperature_c'),-30,140),_number(payload.get('disk_free_mb'),0,1e9),
       str(payload.get('playback_state') or '')[:50], str(payload.get('sync_status') or '')[:40],
       int(payload['browser_running']) if isinstance(payload.get('browser_running'),bool) else None))
    ack=[]
    events=payload.get('stability_events')
    if isinstance(events,list):
        for obj in events[:40]:
            if not isinstance(obj,dict): continue
            uid=str(obj.get('id') or '')
            kind=str(obj.get('type') or '')
            if not (len(uid)==32 and all(c in '0123456789abcdef' for c in uid) and kind in ALLOWED_EVENTS):
                continue
            at=_number(obj.get('at'))
            if at is None or abs(at-received_at)>30*86400: at=received_at
            sev=str(obj.get('severity') or 'info')
            if sev not in ('info','warning','error'): sev='info'
            conn.execute("""INSERT OR IGNORE INTO stability_events
             (tv_id,event_uid,occurred_at,received_at,event_type,severity,reason,content_name)
             VALUES(?,?,?,?,?,?,?,?)""",
             (tv_id,uid,at,received_at,kind,sev,str(obj.get('reason') or '')[:500],
              str(obj.get('content') or '')[:300]))
            ack.append(uid)
    if received_at-_last_cleanup > 21600:
        conn.execute('DELETE FROM stability_samples WHERE observed_at < ?', (received_at-SAMPLE_RETENTION_DAYS*86400,))
        conn.execute('DELETE FROM stability_events WHERE received_at < ?', (received_at-EVENT_RETENTION_DAYS*86400,))
        _last_cleanup=received_at
    return ack

def window(period='night', day_string=''):
    now=datetime.now(TZ)
    if period=='24h': return now.timestamp()-86400,now.timestamp(),'Últimas 24 horas','24h'
    if period=='7d': return now.timestamp()-7*86400,now.timestamp(),'Últimos 7 dias','7d'
    if period=='30d': return now.timestamp()-30*86400,now.timestamp(),'Últimos 30 dias','30d'
    try:
        day=date.fromisoformat(day_string) if day_string else now.date()
        if day>now.date() or day<now.date()-timedelta(days=365): raise ValueError
    except ValueError:
        day=now.date()
    start=datetime(day.year,day.month,day.day,8,tzinfo=TZ)-timedelta(hours=14)
    end=datetime(day.year,day.month,day.day,8,tzinfo=TZ)
    end_ts=min(end.timestamp(),now.timestamp())
    return start.timestamp(),max(start.timestamp(),end_ts),f'Noite de {start:%d/%m} para {end:%d/%m/%Y}','night'

def fmt(ts):
    return datetime.fromtimestamp(float(ts),TZ).strftime('%d/%m %H:%M:%S')

def offline_intervals(conn,tv_id,start,end):
    """Estimativa conservadora baseada nas transições do monitor do servidor."""
    if end<=start:return []
    prior=conn.execute("SELECT event_type FROM tv_event_log WHERE tv_id=? AND created_at<? AND event_type LIKE 'health_%' ORDER BY created_at DESC LIMIT 1",(tv_id,start)).fetchone()
    active=bool(prior and prior['event_type']=='health_offline')
    begin=start if active else None
    rows=conn.execute("SELECT created_at,event_type FROM tv_event_log WHERE tv_id=? AND created_at>=? AND created_at<? AND event_type LIKE 'health_%' ORDER BY created_at ASC",(tv_id,start,end)).fetchall()
    out=[]
    for r in rows:
        t=float(r['created_at']); kind=r['event_type']
        if kind=='health_offline' and not active:
            active=True;begin=t
        elif kind!='health_offline' and active:
            if begin is not None and t>begin:out.append((begin,t))
            active=False;begin=None
    if active and begin is not None:out.append((begin,end))
    return out

def summarize(conn,tv_id,start,end):
    rows=conn.execute('SELECT * FROM stability_events WHERE tv_id=? AND occurred_at>=? AND occurred_at<? ORDER BY occurred_at ASC,id ASC',(tv_id,start,end)).fetchall()
    samples=conn.execute('SELECT * FROM stability_samples WHERE tv_id=? AND observed_at>=? AND observed_at<? ORDER BY observed_at ASC',(tv_id,start,end)).fetchall()
    offs=offline_intervals(conn,tv_id,start,end)
    secs=sum(max(0,b-a) for a,b in offs)
    span=max(0,end-start)
    reboots=[x for x in rows if x['event_type']=='system_reboot']
    reboot=len(reboots)
    remote_reboots=0
    for e in reboots:
        # O reboot acontece depois do ACK remoto; tolerância de até 10 minutos.
        command=conn.execute("""SELECT 1 FROM player_commands
            WHERE tv_id=? AND command='reboot' AND status='done'
            AND created_at<=? AND created_at>=? LIMIT 1""",
            (tv_id,e['occurred_at'],e['occurred_at']-600)).fetchone()
        if command:remote_reboots+=1
    unexplained_reboots=reboot-remote_reboots
    player=sum(x['event_type']=='player_restart' for x in rows)
    browser=[x for x in rows if x['event_type']=='browser_restart']
    watchdog=sum('watchdog:' in str(x['reason']).lower() for x in browser)
    def agg(field,fn):
        values=[float(r[field]) for r in samples if r[field] is not None]
        return round(fn(values),1) if values else None
    online=bool(samples)
    availability=round(max(0,1-secs/span)*100,2) if online and span>0 else None
    if not online: health='no_data';label='Sem dados de amostragem'
    elif unexplained_reboots>0 or (availability is not None and availability<98) or watchdog>5:health='error';label='Requer atenção'
    elif watchdog>2 or (availability is not None and availability<99.5):health='warning';label='Atenção'
    else:health='ok';label='Sem alertas críticos identificados'
    def graphic(field):
        values=[(float(s['observed_at']),s[field]) for s in samples if s[field] is not None]
        if len(values)>240:
            step=max(1,len(values)//240);values=values[::step]
        if not values:return ''
        low=min(float(x[1]) for x in values); high=max(float(x[1]) for x in values)
        if high==low:high+=1
        tspan=max(1,end-start)
        return ' '.join(f"{(t-start)/tspan*1000:.1f},{95-(float(v)-low)/(high-low)*86:.1f}" for t,v in values)
    eventlist=[{'at':fmt(r['occurred_at']),'ts':r['occurred_at'],'type':r['event_type'],'severity':r['severity'],
                'label':{'monitor_started':'Coleta iniciada','system_reboot':'Reboot detectado','player_restart':'Player reiniciado',
                         'browser_start':'Navegador iniciado','browser_restart':'Navegador reiniciado'}.get(r['event_type'],r['event_type']),
                'reason':r['reason'],'content':r['content_name']} for r in rows]
    for event in eventlist:
        if event['type']=='system_reboot':
            remote=conn.execute("""SELECT 1 FROM player_commands WHERE tv_id=? AND command='reboot'
                AND status='done' AND created_at<=? AND created_at>=? LIMIT 1""",
                (tv_id,event['ts'],event['ts']-600)).fetchone()
            if remote:
                event['label']='Reboot comandado pela TI';event['severity']='info'
            else:
                event['label']='Reboot sem comando remoto identificado';event['severity']='warning'
    for a,b in offs:eventlist.append({'at':fmt(a),'ts':a,'type':'offline','severity':'warning','label':'Sem comunicação','reason':f'Duração estimada: {int(b-a)}s','content':''})
    eventlist.sort(key=lambda e:e['ts'],reverse=True)
    return {'tv_id':tv_id,'health':health,'health_label':label,'reboots':reboot,
            'remote_reboots':remote_reboots,'unexplained_reboots':unexplained_reboots,'player_restarts':player,
            'browser_restarts':len(browser),'watchdog':watchdog,'offline_seconds':int(secs),
            'availability':availability,'cpu_avg':agg('cpu_percent',lambda a:sum(a)/len(a)),
            'ram_avg':agg('memory_percent',lambda a:sum(a)/len(a)),
            'temp_max':agg('temperature_c',max),'disk_min_mb':agg('disk_free_mb',min),
            'samples':len(samples),'events':eventlist[:500],
            'graph_cpu':graphic('cpu_percent'),'graph_ram':graphic('memory_percent'),
            'graph_temp':graphic('temperature_c'),'from_text':fmt(start),'to_text':fmt(end),
            'notes':'Disponibilidade estimada por transições do monitor. Períodos sem coleta podem não ser observados.'}
