import { useState, useEffect } from 'react'
import { Home, Hospital, Minus, Plus, ShieldAlert } from 'lucide-react'

type MockMapProps = {
  isRiverWarning?: boolean
}

type Team = {
  id: string
  label: string
  x: number // in px (0-900)
  y: number // in px (0-620)
  status: 'ACTIVE' | 'STAGING'
  color: string
}

const INITIAL_TEAMS: Team[] = [
  { id: 'UNIT-A1', label: 'A1', x: 630, y: 390, status: 'ACTIVE', color: '#00d4ff' },
  { id: 'UNIT-A2', label: 'A2', x: 560, y: 320, status: 'STAGING', color: '#ffaa00' },
  { id: 'UNIT-B1', label: 'B1', x: 130, y: 60, status: 'ACTIVE', color: '#00d4ff' },
  { id: 'UNIT-B2', label: 'B2', x: 160, y: 140, status: 'ACTIVE', color: '#00d4ff' },
  { id: 'UNIT-C1', label: 'C1', x: 360, y: 220, status: 'ACTIVE', color: '#00d4ff' },
  { id: 'UNIT-C2', label: 'C2', x: 480, y: 460, status: 'STAGING', color: '#ffaa00' },
]

const shelters = [
  { name: 'Puri', cap: '73%', x: 68, y: 36 },
  { name: 'Balasore', cap: '61%', x: 53, y: 18 },
  { name: 'Cuttack', cap: '88%', x: 40, y: 39 },
  { name: 'Bhubaneswar', cap: '67%', x: 49, y: 49 },
]

export function MockMap({ isRiverWarning = false }: MockMapProps) {
  const [teamsState, setTeamsState] = useState<Team[]>(INITIAL_TEAMS)
  const [tooltipVisibleTeams, setTooltipVisibleTeams] = useState<Record<string, boolean>>({})
  const [isB2Distress, setIsB2Distress] = useState(false)

  // GPS Drift logic
  useEffect(() => {
    const interval = setInterval(() => {
      setTeamsState((prevTeams) => {
        return prevTeams.map((team) => {
          // Delta range: -3px to +3px
          // UNIT-C1 moves more deliberately: -6px to +6px
          const maxDelta = team.id === 'UNIT-C1' ? 6 : 3
          const dx = Math.random() * (maxDelta * 2) - maxDelta
          const dy = Math.random() * (maxDelta * 2) - maxDelta

          let newX = team.x + dx
          let newY = team.y + dy

          // Clamp to map bounds with 15px margin to avoid clipping
          const marginX = 15
          const marginY = 15
          newX = Math.max(marginX, Math.min(900 - marginX, newX))
          newY = Math.max(marginY, Math.min(620 - marginY, newY))

          const distance = Math.sqrt(dx * dx + dy * dy)

          if (distance > 4) {
            // Trigger GPS UPDATE tooltip for 1.5 seconds
            setTimeout(() => {
              setTooltipVisibleTeams((prev) => ({
                ...prev,
                [team.id]: true,
              }))
              setTimeout(() => {
                setTooltipVisibleTeams((prev) => ({
                  ...prev,
                  [team.id]: false,
                }))
              }, 1500)
            }, 0)
          }

          return {
            ...team,
            x: newX,
            y: newY,
          }
        })
      })
    }, 8000)

    return () => clearInterval(interval)
  }, [])

  // B2 Distress simulation
  useEffect(() => {
    const distressTimer = setTimeout(() => {
      setIsB2Distress(true)
      const clearTimer = setTimeout(() => {
        setIsB2Distress(false)
      }, 4000)
      return () => clearTimeout(clearTimer)
    }, 180000) // 3 minutes after mount

    return () => clearTimeout(distressTimer)
  }, [])

  return (
    <div className="map-frame panel">
      <style>{`
        .team-marker-custom {
          position: absolute;
          transform: translate(-50%, -50%);
          z-index: 2;
          width: 14px;
          height: 14px;
          border-radius: 999px;
          transition: left 1.2s ease, top 1.2s ease, background-color 0.3s ease, box-shadow 0.3s ease;
        }
        .team-marker-custom.active {
          background: #00d4ff;
          box-shadow: 0 0 16px rgba(0, 212, 255, 0.8);
        }
        .team-marker-custom.staging {
          background: #ffaa00;
          box-shadow: 0 0 16px rgba(255, 170, 0, 0.8);
        }
        .team-marker-custom.distress {
          animation: flash-red-anim 0.5s infinite alternate;
        }
        
        @keyframes flash-red-anim {
          0% {
            background: #ff3b3b;
            box-shadow: 0 0 16px rgba(255, 59, 59, 0.9);
          }
          100% {
            background: #4a0000;
            box-shadow: 0 0 4px rgba(255, 59, 59, 0.3);
          }
        }

        .marker-pulse-custom {
          position: absolute;
          inset: -9px;
          border-radius: 50%;
          animation: marker-pulse-anim 1.8s infinite;
        }
        .marker-pulse-custom.active {
          border: 1px solid rgba(0, 212, 255, 0.85);
        }
        .marker-pulse-custom.distress {
          border: 1px solid rgba(255, 59, 59, 0.85);
        }

        @keyframes marker-pulse-anim {
          0% {
            transform: scale(0.5);
            opacity: 1;
          }
          100% {
            transform: scale(1.4);
            opacity: 0;
          }
        }

        .team-label-custom {
          position: absolute;
          left: 16px;
          top: -4px;
          padding: 2px 5px;
          background: rgba(10, 13, 20, 0.72);
          font: 700 12px/1 var(--font-heading);
          white-space: nowrap;
          border-radius: 2px;
          transition: color 0.3s, border 0.3s;
        }
        .team-label-custom.active {
          color: #00d4ff;
          border: 1px solid rgba(0, 212, 255, 0.4);
        }
        .team-label-custom.staging {
          color: #ffaa00;
          border: 1px solid rgba(255, 170, 0, 0.4);
        }
        .team-label-custom.distress {
          color: #ff3b3b;
          border: 1px solid rgba(255, 59, 59, 0.6);
        }

        .gps-tooltip {
          position: absolute;
          bottom: 24px;
          left: 50%;
          transform: translateX(-50%);
          background: rgba(0, 212, 255, 0.95);
          color: #0a0d14;
          font: 800 9px var(--font-heading);
          padding: 2px 5px;
          border-radius: 3px;
          white-space: nowrap;
          pointer-events: none;
          box-shadow: 0 2px 8px rgba(0, 212, 255, 0.4);
          animation: fade-in-out 1.5s forwards;
          z-index: 10;
        }

        .distress-tooltip {
          position: absolute;
          bottom: 24px;
          left: 50%;
          transform: translateX(-50%);
          background: #ff3b3b;
          color: #ffffff;
          font: 800 10px var(--font-heading);
          padding: 3px 6px;
          border-radius: 3px;
          white-space: nowrap;
          pointer-events: none;
          box-shadow: 0 0 12px rgba(255, 59, 59, 0.6);
          z-index: 10;
          animation: pulse-distress 1s infinite alternate;
        }

        @keyframes pulse-distress {
          0% { transform: translateX(-50%) scale(1); }
          100% { transform: translateX(-50%) scale(1.05); }
        }

        @keyframes fade-in-out {
          0% { opacity: 0; transform: translate(-50%, 4px); }
          15% { opacity: 1; transform: translate(-50%, 0); }
          85% { opacity: 1; }
          100% { opacity: 0; transform: translate(-50%, -4px); }
        }

        .pulse-red-twice-anim {
          animation: pulse-red-twice 1.5s ease-in-out 2;
        }

        @keyframes pulse-red-twice {
          0%, 100% { fill: rgba(255, 59, 59, 0.24); stroke: rgba(255, 59, 59, 0.68); }
          50% { fill: rgba(255, 0, 0, 0.7); stroke: rgba(255, 0, 0, 1); filter: drop-shadow(0 0 10px rgba(255, 0, 0, 0.8)); }
        }

        @keyframes map-stat-flash-red-anim {
          0%, 100% { background: rgba(10, 13, 20, 0.38); border-color: rgba(58, 74, 107, 0.75); color: var(--text-secondary); }
          50% { background: rgba(255, 59, 59, 0.4); border-color: #ff3b3b; color: #fff; box-shadow: 0 0 8px rgba(255, 59, 59, 0.6); }
        }
        .map-stat-flash-red {
          animation: map-stat-flash-red-anim 0.5s infinite;
        }
      `}</style>

      <div className="map-header">
        <div>
          <span className="eyebrow">LIVE OPERATIONAL MAP</span>
          <h1>ODISHA COAST RESPONSE THEATRE</h1>
        </div>
        <div className="map-stats">
          <span>WIND 142 KM/H</span>
          <span>SURGE +2.8M</span>
          <span className={isRiverWarning ? 'map-stat-flash-red' : ''} style={{ transition: 'all 0.3s' }}>
            {isRiverWarning ? 'MAHANADI GAUGE: 98.7%' : 'ZONE 7 HIGH'}
          </span>
        </div>
      </div>

      <div className="map-canvas" role="img" aria-label="Mock Odisha coastline disaster response map">
        <svg className="base-map" viewBox="0 0 900 620" preserveAspectRatio="none">
          <defs>
            <pattern id="grid" width="54" height="54" patternUnits="userSpaceOnUse">
              <path d="M 54 0 L 0 0 0 54" fill="none" stroke="rgba(58,74,107,.24)" strokeWidth="1" />
            </pattern>
            <filter id="glow">
              <feGaussianBlur stdDeviation="4" result="blur" />
              <feMerge>
                <feMergeNode in="blur" />
                <feMergeNode in="SourceGraphic" />
              </feMerge>
            </filter>
          </defs>
          <rect width="900" height="620" fill="url(#grid)" />
          <path
            d="M0 0H900V620H644C657 573 635 548 647 506C665 447 734 425 721 365C707 299 642 284 642 223C642 151 714 120 690 52C681 27 663 12 641 0Z"
            fill="rgba(0, 212, 255, .07)"
          />
          <path
            d="M640 0C676 54 624 105 633 165C641 222 688 260 694 323C700 387 658 424 632 477C610 522 623 566 595 620"
            fill="none"
            stroke="rgba(0, 212, 255, .54)"
            strokeWidth="4"
            filter="url(#glow)"
          />
          <path d="M66 470L226 418L356 454L497 391L622 341" stroke="rgba(0,230,118,.78)" strokeWidth="4" fill="none" strokeDasharray="10 8" />
          <path d="M108 188L264 251L398 240L535 301L681 286" stroke="rgba(0,230,118,.78)" strokeWidth="4" fill="none" strokeDasharray="10 8" />
          
          {/* Flood hazard zone overlays */}
          <path
            d="M563 177C626 151 703 174 729 229C685 258 604 267 554 232Z"
            fill="rgba(255,59,59,.28)"
            stroke="rgba(255,59,59,.8)"
            className={isRiverWarning ? 'pulse-red-twice-anim' : ''}
          />
          <path
            d="M513 315C586 285 672 305 706 368C662 414 555 417 498 369Z"
            fill="rgba(255,59,59,.26)"
            stroke="rgba(255,59,59,.75)"
            className={isRiverWarning ? 'pulse-red-twice-anim' : ''}
          />
          <path
            d="M410 463C492 443 585 474 612 545C550 591 449 575 391 521Z"
            fill="rgba(255,59,59,.24)"
            stroke="rgba(255,59,59,.68)"
            className={isRiverWarning ? 'pulse-red-twice-anim' : ''}
          />
          
          <text x="124" y="86" className="map-label">BALASORE</text>
          <text x="286" y="342" className="map-label">BHUBANESWAR</text>
          <text x="590" y="390" className="map-label">PURI COAST</text>
          <text x="448" y="570" className="map-label">ZONE 7</text>
        </svg>

        {teamsState.map((team) => {
          const isDistress = team.id === 'UNIT-B2' && isB2Distress
          const showGpsTooltip = tooltipVisibleTeams[team.id]
          const markerClass = `team-marker-custom ${isDistress ? 'distress' : team.status === 'ACTIVE' ? 'active' : 'staging'}`
          const labelClass = `team-label-custom ${isDistress ? 'distress' : team.status === 'ACTIVE' ? 'active' : 'staging'}`
          const pulseClass = `marker-pulse-custom ${isDistress ? 'distress' : 'active'}`

          return (
            <div
              className={markerClass}
              style={{
                left: `${(team.x / 900) * 100}%`,
                top: `${(team.y / 620) * 100}%`,
              }}
              key={team.id}
            >
              {(team.status === 'ACTIVE' || isDistress) && (
                <span className={pulseClass} />
              )}
              <span className={labelClass}>{team.label}</span>
              {showGpsTooltip && (
                <div className="gps-tooltip">GPS UPDATE</div>
              )}
              {isDistress && (
                <div className="distress-tooltip">⚠ DISTRESS</div>
              )}
            </div>
          )
        })}

        {shelters.map((shelter) => (
          <div className="shelter-marker" style={{ left: `${shelter.x}%`, top: `${shelter.y}%` }} key={shelter.name}>
            <Home size={16} />
            <span>{shelter.name} {shelter.cap}</span>
          </div>
        ))}

        <div className="risk-marker" style={{ left: '58%', top: '53%' }}>
          <ShieldAlert size={18} />
          <span>Hospital power risk</span>
        </div>

        <div className="map-legend">
          <span><i className="legend-dot team" style={{ backgroundColor: '#00d4ff', boxShadow: '0 0 8px #00d4ff' }} /> Active Team</span>
          <span><i className="legend-dot team" style={{ backgroundColor: '#ffaa00', boxShadow: '0 0 8px #ffaa00' }} /> Staging Team</span>
          <span><i className="legend-dot shelter" /> Shelter</span>
          <span><i className="legend-line" /> Evac route</span>
          <span><i className="legend-zone" /> Perimeter</span>
        </div>

        <div className="zoom-controls" aria-label="Map zoom controls">
          <button type="button" aria-label="Zoom in"><Plus size={17} /></button>
          <button type="button" aria-label="Zoom out"><Minus size={17} /></button>
        </div>

        <div className="hospital-tag">
          <Hospital size={14} />
          SCB MEDICAL LINK FLAGGED
        </div>
      </div>
    </div>
  )
}
