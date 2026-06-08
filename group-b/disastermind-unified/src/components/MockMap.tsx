import { Home, Hospital, Minus, Plus, ShieldAlert } from 'lucide-react'

const teams = [
  { id: 'TEAM-01', x: 28, y: 22 },
  { id: 'TEAM-02', x: 48, y: 28 },
  { id: 'TEAM-03', x: 62, y: 43 },
  { id: 'TEAM-04', x: 38, y: 56 },
  { id: 'TEAM-05', x: 70, y: 63 },
  { id: 'TEAM-06', x: 54, y: 76 },
]

const shelters = [
  { name: 'Puri', cap: '73%', x: 68, y: 36 },
  { name: 'Balasore', cap: '61%', x: 53, y: 18 },
  { name: 'Cuttack', cap: '88%', x: 40, y: 39 },
  { name: 'Bhubaneswar', cap: '67%', x: 49, y: 49 },
]

export function MockMap() {
  return (
    <div className="map-frame panel">
      <div className="map-header">
        <div>
          <span className="eyebrow">LIVE OPERATIONAL MAP</span>
          <h1>ODISHA COAST RESPONSE THEATRE</h1>
        </div>
        <div className="map-stats">
          <span>WIND 142 KM/H</span>
          <span>SURGE +2.8M</span>
          <span>ZONE 7 HIGH</span>
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
          <path d="M563 177C626 151 703 174 729 229C685 258 604 267 554 232Z" fill="rgba(255,59,59,.28)" stroke="rgba(255,59,59,.8)" />
          <path d="M513 315C586 285 672 305 706 368C662 414 555 417 498 369Z" fill="rgba(255,59,59,.26)" stroke="rgba(255,59,59,.75)" />
          <path d="M410 463C492 443 585 474 612 545C550 591 449 575 391 521Z" fill="rgba(255,59,59,.24)" stroke="rgba(255,59,59,.68)" />
          <text x="124" y="86" className="map-label">BALASORE</text>
          <text x="286" y="342" className="map-label">BHUBANESWAR</text>
          <text x="590" y="390" className="map-label">PURI COAST</text>
          <text x="448" y="570" className="map-label">ZONE 7</text>
        </svg>

        {teams.map((team) => (
          <div className="team-marker" style={{ left: `${team.x}%`, top: `${team.y}%` }} key={team.id}>
            <span className="marker-pulse" />
            <span className="team-label">{team.id}</span>
          </div>
        ))}

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
          <span><i className="legend-dot team" /> Field team</span>
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
