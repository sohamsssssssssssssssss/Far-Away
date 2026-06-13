import type { GpsReading } from '@/lib/mapTypes'
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table'
import { cn } from '@/lib/utils'

const TEAM_META: Record<string, { type: string; base: string }> = {
  'UNIT-A1': { type: 'Rescue · NDRF', base: 'Puri Coast' },
  'UNIT-A2': { type: 'Medical Response', base: 'Puri South' },
  'UNIT-B1': { type: 'Evac Transport', base: 'Balasore Hub' },
  'UNIT-B2': { type: 'Structural Eng.', base: 'Balasore North' },
  'UNIT-C1': { type: 'Supply Drops', base: 'Cuttack Relief' },
}

const STATUS_META: Record<
  GpsReading['status'],
  { label: string; dot: string; text: string; border: string; pulse?: boolean }
> = {
  active: { label: 'Deployed', dot: 'bg-success', text: 'text-success', border: 'border-success/30' },
  staged: { label: 'En Route', dot: 'bg-warning', text: 'text-warning', border: 'border-warning/30' },
  distress: {
    label: 'Critical',
    dot: 'bg-error',
    text: 'text-error',
    border: 'border-error/30',
    pulse: true,
  },
  offline: {
    label: 'Offline',
    dot: 'bg-outline',
    text: 'text-on-surface-variant',
    border: 'border-outline-variant/40',
  },
}

function elapsed(timestamp: string): string {
  const start = new Date(timestamp).getTime()
  if (Number.isNaN(start)) return '—'
  const mins = Math.max(0, Math.floor((Date.now() - start) / 60000))
  if (mins < 60) return `${mins}m`
  return `${String(Math.floor(mins / 60)).padStart(2, '0')}h ${String(mins % 60).padStart(2, '0')}m`
}

export function DeploymentsTable({ teams }: { teams: Record<string, GpsReading> }) {
  const rows = Object.values(teams)

  return (
    <div className="overflow-x-auto">
      <Table>
        <TableHeader>
          <TableRow className="border-outline-variant/15 bg-surface-container-low hover:bg-transparent">
            <TableHead>Unit ID</TableHead>
            <TableHead>Type</TableHead>
            <TableHead>Location</TableHead>
            <TableHead>Status</TableHead>
            <TableHead className="text-right">Duration</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {rows.map((team, i) => {
            const meta = TEAM_META[team.team_id] ?? {
              type: 'Field Unit',
              base: `${team.location.lat.toFixed(2)}, ${team.location.lon.toFixed(2)}`,
            }
            const status = STATUS_META[team.status]
            return (
              <TableRow
                key={team.team_id}
                className={cn(
                  'hover:bg-surface/60',
                  i % 2 === 0 ? 'bg-surface/20' : 'bg-surface',
                )}
              >
                <TableCell className="font-mono text-data-mono tabular-nums text-primary">
                  {team.team_id}
                </TableCell>
                <TableCell>{meta.type}</TableCell>
                <TableCell>{meta.base}</TableCell>
                <TableCell>
                  <span
                    className={cn(
                      'inline-flex items-center gap-1.5 rounded border bg-surface px-2 py-0.5 text-label-sm uppercase',
                      status.text,
                      status.border,
                    )}
                  >
                    <span className={cn('h-1.5 w-1.5 rounded-full', status.dot, status.pulse && 'animate-pulse')} />
                    {status.label}
                  </span>
                </TableCell>
                <TableCell className="text-right font-mono text-data-mono tabular-nums text-on-surface-variant">
                  {elapsed(team.timestamp)}
                </TableCell>
              </TableRow>
            )
          })}
        </TableBody>
      </Table>
    </div>
  )
}
