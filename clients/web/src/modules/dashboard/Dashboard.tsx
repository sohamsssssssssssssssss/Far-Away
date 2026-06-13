import { useEffect, useMemo, useState } from 'react'
import { useApiStatus } from '@/hooks/useApiStatus'
import { useEscalations } from '@/hooks/useEscalations'
import { connectWebSocket } from '@/lib/disasterApi'
import { SYNTHETIC_MAP_STATE } from '@/lib/mapTypes'
import type { MapState, EscalationItem } from '@/lib/mapTypes'
import { Card, CardHeader, CardTitle } from '@/components/ui/card'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Icon } from '@/components/ui/icon'
import { cn } from '@/lib/utils'
import { LiveMap } from './components/LiveMap'
import { DeploymentsTable } from './components/DeploymentsTable'

/* ------------------------------------------------------------------ helpers */

function useNow(intervalMs = 1000) {
  const [now, setNow] = useState(() => Date.now())
  useEffect(() => {
    const t = window.setInterval(() => setNow(Date.now()), intervalMs)
    return () => window.clearInterval(t)
  }, [intervalMs])
  return now
}

const PRIORITY_META: Record<
  EscalationItem['priority'],
  { label: string; border: string; text: string }
> = {
  CRITICAL: { label: 'Priority 1', border: 'border-l-error', text: 'text-error' },
  HIGH: { label: 'Priority 2', border: 'border-l-on-tertiary-container', text: 'text-on-tertiary-container' },
  MEDIUM: { label: 'Priority 3', border: 'border-l-secondary', text: 'text-secondary' },
}

const TRIGGER_ICON: Record<string, string> = {
  MANDATORY_EVACUATION: 'crisis_alert',
  CROSS_STATE_RESOURCE: 'local_shipping',
  REQUISITION_INFRASTRUCTURE: 'apartment',
  CRITICAL_INFRASTRUCTURE: 'electric_bolt',
  MEDIA_BROADCAST: 'campaign',
  ARMED_FORCES: 'shield',
}

function countdown(item: EscalationItem, now: number): string {
  if (item.timeoutMs === Infinity) return 'MANUAL'
  const remaining = item.timeoutMs - (now - item.createdAt)
  if (remaining <= 0) return 'EXPIRED'
  const total = Math.floor(remaining / 1000)
  const m = String(Math.floor(total / 60)).padStart(2, '0')
  const s = String(total % 60).padStart(2, '0')
  return `T-${m}:${s}`
}

/* ------------------------------------------------------------ KPI card */

interface KpiProps {
  label: string
  value: number | string
  icon: string
  hint: string
  hintIcon: string
  tone?: 'default' | 'critical'
}

function KpiCard({ label, value, icon, hint, hintIcon, tone = 'default' }: KpiProps) {
  const critical = tone === 'critical'
  return (
    <Card
      className={cn(
        'flex flex-col justify-between p-4',
        critical && 'border-error/20 bg-error-container/30',
      )}
    >
      <div className="mb-2 flex items-start justify-between">
        <span
          className={cn(
            'text-label-md uppercase',
            critical ? 'text-on-error-container' : 'text-on-surface-variant',
          )}
        >
          {label}
        </span>
        <Icon name={icon} className={cn('text-[22px]', critical ? 'text-on-error-container' : 'text-primary')} />
      </div>
      <div
        className={cn(
          'text-headline-lg',
          critical ? 'text-on-error-container' : 'text-primary',
        )}
      >
        {value}
      </div>
      <div
        className={cn(
          'mt-1 flex items-center gap-1 text-body-sm',
          critical ? 'text-on-error-container' : 'text-on-surface-variant',
        )}
      >
        <Icon name={hintIcon} className="text-[16px]" />
        {hint}
      </div>
    </Card>
  )
}

/* ------------------------------------------------ escalation queue item */

interface QueueItemProps {
  item: EscalationItem
  now: number
  onDispatch: (id: string) => void
  onAcknowledge: (id: string) => void
}

function QueueItem({ item, now, onDispatch, onAcknowledge }: QueueItemProps) {
  const meta = PRIORITY_META[item.priority]
  const icon = TRIGGER_ICON[item.trigger] ?? 'warning'
  return (
    <div
      className={cn(
        'cursor-pointer rounded border border-l-4 border-outline-variant/30 bg-surface p-3 transition-colors hover:bg-surface-container-highest',
        meta.border,
      )}
    >
      <div className="mb-1 flex items-start justify-between gap-2">
        <span className={cn('flex items-center gap-1.5 text-label-md uppercase', meta.text)}>
          <Icon name={icon} className="text-[16px]" />
          {meta.label} · {item.trigger.replace(/_/g, ' ').toLowerCase()}
        </span>
        <span className="shrink-0 font-mono text-label-sm tabular-nums text-on-surface-variant">
          {countdown(item, now)}
        </span>
      </div>
      <h3 className="mb-1 text-body-md font-bold text-primary">{item.zone}</h3>
      <p className="line-clamp-2 text-body-sm text-on-surface-variant">{item.memo.situation}</p>
      {item.status === 'PENDING' && (
        <div className="mt-2 flex gap-2">
          <Button size="sm" variant="accent" onClick={() => onDispatch(item.id)}>
            Dispatch
          </Button>
          <Button size="sm" variant="outline" onClick={() => onAcknowledge(item.id)}>
            Acknowledge
          </Button>
        </div>
      )}
    </div>
  )
}

/* ------------------------------------------------------------- Dashboard */

export function Dashboard() {
  const { status, setWsState } = useApiStatus()
  const { escalations, pending, approve, overrideItem } = useEscalations()
  const [mapState, setMapState] = useState<MapState>(SYNTHETIC_MAP_STATE)
  const now = useNow(1000)

  // ── Live telemetry: drive the map + unit positions from the Group A socket
  useEffect(() => {
    const disconnect = connectWebSocket(
      (message) => {
        if (message.topic === 'tier3.iot_telemetry') {
          const p = message.payload as Record<string, unknown> | undefined
          if (p?.kind === 'gps_beacon') {
            const readings = (p.readings ?? []) as Array<{
              team_id: string
              location: { lat: number; lon: number }
              status?: 'active' | 'staged' | 'distress' | 'offline'
              timestamp: string
            }>
            readings.forEach((r) =>
              setMapState((prev) => ({
                ...prev,
                teams: {
                  ...prev.teams,
                  [r.team_id]: {
                    team_id: r.team_id,
                    location: r.location,
                    status: r.status ?? 'active',
                    timestamp: r.timestamp,
                  },
                },
              })),
            )
          }
        }
        if (message.topic === 'tier2.prediction') {
          const p = message.payload as Record<string, unknown> | undefined
          if (p?.risk_cells) {
            setMapState((prev) => ({ ...prev, riskCells: p.risk_cells as typeof prev.riskCells }))
          }
        }
      },
      (state) => setWsState(state),
    )
    return () => disconnect()
  }, [setWsState])

  // ── GPS drift simulation keeps the map alive when the backend is offline
  useEffect(() => {
    const t = window.setInterval(() => {
      setMapState((prev) => {
        const teams = { ...prev.teams }
        Object.keys(teams).forEach((id) => {
          const maxDelta = id === 'UNIT-C1' ? 0.006 : 0.003
          teams[id] = {
            ...teams[id],
            location: {
              lat: teams[id].location.lat + (Math.random() * 2 - 1) * maxDelta,
              lon: teams[id].location.lon + (Math.random() * 2 - 1) * maxDelta,
            },
          }
        })
        return { ...prev, teams }
      })
    }, 8000)
    return () => window.clearInterval(t)
  }, [])

  const wsLive = status.wsState === 'connected'
  const unitCount = Object.keys(mapState.teams).length
  const activeUnits = Object.values(mapState.teams).filter((t) => t.status === 'active').length
  const highRiskZones = useMemo(
    () => mapState.riskCells.filter((c) => c.probability >= 0.7).length,
    [mapState.riskCells],
  )
  const criticalCount = pending.filter((e) => e.priority === 'CRITICAL').length

  return (
    <div className="dm-scroll h-full overflow-y-auto bg-surface p-gutter md:p-margin-desktop">
      <div className="mx-auto flex h-full max-w-[1440px] flex-col gap-6">
        {/* Header */}
        <div className="flex shrink-0 flex-col justify-between gap-3 md:flex-row md:items-end">
          <div>
            <h1 className="text-headline-lg text-primary">Commander Dashboard</h1>
            <p className="mt-1 text-body-md text-on-surface-variant">
              Sector 7 Command · Cyclone Remal Response · Odisha Coast
            </p>
          </div>
          <Badge variant={status.backendOnline ? 'success' : 'warning'} className="self-start md:self-auto">
            <span
              className={cn(
                'h-2 w-2 rounded-full',
                status.backendOnline ? 'animate-pulse bg-success' : 'bg-on-tertiary-container',
              )}
            />
            {status.backendOnline ? 'Group A Backend Live' : 'Simulation Mode'}
          </Badge>
        </div>

        {/* KPI row */}
        <div className="grid shrink-0 grid-cols-1 gap-6 md:grid-cols-3">
          <KpiCard
            label="Active Incidents"
            value={highRiskZones}
            icon="local_fire_department"
            hintIcon="arrow_upward"
            hint={`${mapState.riskCells.length} risk zones tracked`}
          />
          <KpiCard
            label="Units Deployed"
            value={unitCount}
            icon="groups"
            hintIcon="check_circle"
            hint={`${activeUnits} active · ${unitCount - activeUnits} staged`}
          />
          <KpiCard
            label="Critical Escalations"
            value={criticalCount}
            icon="warning"
            hintIcon="priority_high"
            hint="Requires immediate review"
            tone="critical"
          />
        </div>

        {/* Map + queue */}
        <div className="grid min-h-[460px] flex-1 grid-cols-1 gap-6 lg:grid-cols-12">
          <Card className="flex flex-col overflow-hidden p-0 lg:col-span-8">
            <CardHeader>
              <CardTitle>Live Operations Map</CardTitle>
              <span
                className={cn(
                  'inline-flex items-center gap-1.5 rounded border px-2 py-1 text-label-sm uppercase',
                  wsLive
                    ? 'border-error/30 bg-surface text-error'
                    : 'border-outline-variant/40 bg-surface text-on-surface-variant',
                )}
              >
                <span className={cn('h-2 w-2 rounded-full', wsLive ? 'animate-pulse bg-error' : 'bg-outline')} />
                {wsLive ? 'Live' : status.wsState === 'connecting' ? 'Connecting' : 'Reconnecting'}
              </span>
            </CardHeader>
            <div className="relative flex-1">
              <LiveMap mapState={mapState} className="absolute inset-0" />
            </div>
          </Card>

          <Card className="flex flex-col overflow-hidden p-0 lg:col-span-4">
            <CardHeader>
              <CardTitle>Escalation Queue</CardTitle>
              <Badge variant="solid">{criticalCount} Critical</Badge>
            </CardHeader>
            <div className="dm-scroll flex flex-1 flex-col gap-2 overflow-y-auto p-3">
              {pending.length === 0 ? (
                <div className="flex flex-1 flex-col items-center justify-center gap-2 py-10 text-center text-on-surface-variant">
                  <Icon name="task_alt" className="text-[32px] text-success" />
                  <p className="text-body-sm">Queue clear — no pending escalations</p>
                </div>
              ) : (
                pending.map((item) => (
                  <QueueItem
                    key={item.id}
                    item={item}
                    now={now}
                    onDispatch={approve}
                    onAcknowledge={(id) => overrideItem(id, 'Acknowledged — manual handling')}
                  />
                ))
              )}
            </div>
          </Card>
        </div>

        {/* Deployments */}
        <Card className="shrink-0 overflow-hidden p-0">
          <CardHeader>
            <CardTitle>Active Deployments Overview</CardTitle>
            <span className="font-mono text-data-mono tabular-nums text-on-surface-variant">
              {escalations.length} events · {unitCount} units
            </span>
          </CardHeader>
          <DeploymentsTable teams={mapState.teams} />
        </Card>
      </div>
    </div>
  )
}
