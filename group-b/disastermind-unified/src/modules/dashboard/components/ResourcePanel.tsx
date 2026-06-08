import { useEffect, useRef, useState } from 'react'
import { Ambulance, Bus, Cross, Helicopter, Sailboat, Shield, type LucideIcon } from 'lucide-react'

type ResourceLabel = 'Boats' | 'Helicopters' | 'Medical Units' | 'Vehicles' | 'NDRF Teams'
type ShelterName = 'Puri Shelter A' | 'Balasore School' | 'Cuttack Stadium'

type ResourceState = {
  label: ResourceLabel
  deployed: number
  total: number
}

type ShelterState = {
  name: ShelterName
  current: number
  max: number
}

const resourceIcons: Record<ResourceLabel, LucideIcon> = {
  Boats: Sailboat,
  Helicopters: Helicopter,
  'Medical Units': Cross,
  Vehicles: Bus,
  'NDRF Teams': Shield,
}

const resourceSnapshots: ResourceState[][] = [
  [
    { label: 'Boats', deployed: 12, total: 18 },
    { label: 'Helicopters', deployed: 4, total: 6 },
    { label: 'Medical Units', deployed: 9, total: 12 },
    { label: 'Vehicles', deployed: 31, total: 46 },
    { label: 'NDRF Teams', deployed: 14, total: 16 },
  ],
  [
    { label: 'Boats', deployed: 15, total: 18 },
    { label: 'Helicopters', deployed: 4, total: 6 },
    { label: 'Medical Units', deployed: 9, total: 12 },
    { label: 'Vehicles', deployed: 33, total: 46 },
    { label: 'NDRF Teams', deployed: 14, total: 16 },
  ],
  [
    { label: 'Boats', deployed: 15, total: 18 },
    { label: 'Helicopters', deployed: 5, total: 6 },
    { label: 'Medical Units', deployed: 10, total: 12 },
    { label: 'Vehicles', deployed: 33, total: 46 },
    { label: 'NDRF Teams', deployed: 15, total: 16 },
  ],
  [
    { label: 'Boats', deployed: 17, total: 18 },
    { label: 'Helicopters', deployed: 5, total: 6 },
    { label: 'Medical Units', deployed: 10, total: 12 },
    { label: 'Vehicles', deployed: 38, total: 46 },
    { label: 'NDRF Teams', deployed: 16, total: 16 },
  ],
  [
    { label: 'Boats', deployed: 18, total: 18 },
    { label: 'Helicopters', deployed: 6, total: 6 },
    { label: 'Medical Units', deployed: 11, total: 12 },
    { label: 'Vehicles', deployed: 40, total: 46 },
    { label: 'NDRF Teams', deployed: 16, total: 16 },
  ],
  [
    { label: 'Boats', deployed: 16, total: 18 },
    { label: 'Helicopters', deployed: 5, total: 6 },
    { label: 'Medical Units', deployed: 10, total: 12 },
    { label: 'Vehicles', deployed: 37, total: 46 },
    { label: 'NDRF Teams', deployed: 15, total: 16 },
  ],
]

const shelterSnapshots: ShelterState[][] = [
  [
    { name: 'Puri Shelter A', current: 847, max: 1160 },
    { name: 'Balasore School', current: 522, max: 850 },
    { name: 'Cuttack Stadium', current: 1186, max: 1300 },
  ],
  [
    { name: 'Puri Shelter A', current: 891, max: 1160 },
    { name: 'Balasore School', current: 568, max: 850 },
    { name: 'Cuttack Stadium', current: 1201, max: 1300 },
  ],
  [
    { name: 'Puri Shelter A', current: 934, max: 1160 },
    { name: 'Balasore School', current: 612, max: 850 },
    { name: 'Cuttack Stadium', current: 1241, max: 1300 },
  ],
  [
    { name: 'Puri Shelter A', current: 978, max: 1160 },
    { name: 'Balasore School', current: 658, max: 850 },
    { name: 'Cuttack Stadium', current: 1268, max: 1300 },
  ],
  [
    { name: 'Puri Shelter A', current: 1021, max: 1160 },
    { name: 'Balasore School', current: 701, max: 850 },
    { name: 'Cuttack Stadium', current: 1289, max: 1300 },
  ],
  [
    { name: 'Puri Shelter A', current: 1044, max: 1160 },
    { name: 'Balasore School', current: 723, max: 850 },
    { name: 'Cuttack Stadium', current: 1300, max: 1300 },
  ],
]

const capacityTone = (pct: number) => (pct >= 90 ? 'danger' : pct >= 70 ? 'warning' : 'success')
const easeOut = (progress: number) => 1 - Math.pow(1 - progress, 3)

function animateValue(
  start: number,
  end: number,
  onUpdate: (value: number) => void,
  onDone: () => void,
) {
  const duration = 600
  const startedAt = performance.now()

  const frame = (now: number) => {
    const progress = Math.min((now - startedAt) / duration, 1)
    const nextValue = Math.round(start + (end - start) * easeOut(progress))

    onUpdate(nextValue)

    if (progress < 1) {
      return requestAnimationFrame(frame)
    }

    onUpdate(end)
    onDone()
    return 0
  }

  return requestAnimationFrame(frame)
}

export function ResourcePanel({ boatsAdjustment = 0 }: { boatsAdjustment?: number }) {
  const [resources, setResources] = useState<ResourceState[]>(resourceSnapshots[0])
  const [shelters, setShelters] = useState<ShelterState[]>(shelterSnapshots[0])
  const [flashingResources, setFlashingResources] = useState<Partial<Record<ResourceLabel, 'increase' | 'max'>>>({})
  const snapshotIndexRef = useRef(0)
  const resourceAnimationFramesRef = useRef<number[]>([])
  const shelterAnimationFramesRef = useRef<number[]>([])
  const flashTimersRef = useRef<number[]>([])

  useEffect(() => {
    const clearAnimations = () => {
      resourceAnimationFramesRef.current.forEach(cancelAnimationFrame)
      shelterAnimationFramesRef.current.forEach(cancelAnimationFrame)
      resourceAnimationFramesRef.current = []
      shelterAnimationFramesRef.current = []
    }

    const flashResource = (label: ResourceLabel, tone: 'increase' | 'max') => {
      setFlashingResources((current) => ({ ...current, [label]: tone }))

      const flashTimer = window.setTimeout(() => {
        setFlashingResources((current) => ({ ...current, [label]: undefined }))
      }, 800)

      flashTimersRef.current.push(flashTimer)
    }

    const advanceSnapshot = () => {
      const nextIndex = snapshotIndexRef.current === resourceSnapshots.length - 1
        ? 1
        : snapshotIndexRef.current + 1
      const nextResources = resourceSnapshots[nextIndex]
      const nextShelters = shelterSnapshots[nextIndex]

      clearAnimations()

      setResources((currentResources) => {
        currentResources.forEach((resource) => {
          const target = nextResources.find((nextResource) => nextResource.label === resource.label)

          if (!target || target.deployed === resource.deployed) {
            return
          }

          if (target.deployed === target.total) {
            flashResource(resource.label, 'max')
          } else if (target.deployed > resource.deployed) {
            flashResource(resource.label, 'increase')
          }

          const frameId = animateValue(
            resource.deployed,
            target.deployed,
            (value) => {
              setResources((animatedResources) =>
                animatedResources.map((animatedResource) =>
                  animatedResource.label === resource.label
                    ? { ...animatedResource, deployed: value }
                    : animatedResource,
                ),
              )
            },
            () => {
              setResources((animatedResources) =>
                animatedResources.map((animatedResource) =>
                  animatedResource.label === resource.label
                    ? { ...animatedResource, deployed: target.deployed }
                    : animatedResource,
                ),
              )
            },
          )

          resourceAnimationFramesRef.current.push(frameId)
        })

        return currentResources.map((resource) => {
          const target = nextResources.find((nextResource) => nextResource.label === resource.label)
          return target ? { ...resource, total: target.total } : resource
        })
      })

      setShelters((currentShelters) => {
        currentShelters.forEach((shelter) => {
          const target = nextShelters.find((nextShelter) => nextShelter.name === shelter.name)

          if (!target || target.current === shelter.current) {
            return
          }

          const frameId = animateValue(
            shelter.current,
            target.current,
            (value) => {
              setShelters((animatedShelters) =>
                animatedShelters.map((animatedShelter) =>
                  animatedShelter.name === shelter.name
                    ? { ...animatedShelter, current: value }
                    : animatedShelter,
                ),
              )
            },
            () => {
              setShelters((animatedShelters) =>
                animatedShelters.map((animatedShelter) =>
                  animatedShelter.name === shelter.name
                    ? { ...animatedShelter, current: target.current }
                    : animatedShelter,
                ),
              )
            },
          )

          shelterAnimationFramesRef.current.push(frameId)
        })

        return currentShelters.map((shelter) => {
          const target = nextShelters.find((nextShelter) => nextShelter.name === shelter.name)
          return target ? { ...shelter, max: target.max } : shelter
        })
      })

      snapshotIndexRef.current = nextIndex
    }

    const intervalTimer = window.setInterval(advanceSnapshot, 45000)

    return () => {
      window.clearInterval(intervalTimer)
      clearAnimations()
      flashTimersRef.current.forEach(window.clearTimeout)
      flashTimersRef.current = []
    }
  }, [])

  return (
    <section className="panel resource-panel">
      <div className="panel-title">
        <h2>RESOURCES</h2>
        <span>THEATRE ASSETS</span>
      </div>
      <div className="resource-list">
        {resources.map(({ label, deployed, total }) => {
          const Icon = resourceIcons[label]
          const finalDeployed = label === 'Boats' ? Math.max(0, deployed + boatsAdjustment) : deployed
          const pct = Math.round((finalDeployed / total) * 100)
          const flashTone = flashingResources[label]
          return (
            <div className="resource-row" key={label}>
              <div className="resource-topline">
                <span className="resource-name"><Icon size={17} /> {label}</span>
                <span
                  className="resource-count"
                  style={{
                    color: flashTone === 'max' ? '#ffaa00' : flashTone === 'increase' ? '#00d4ff' : undefined,
                    transition: 'color 240ms ease',
                  }}
                >
                  {finalDeployed} / {total}
                </span>
              </div>
              <div className="util-bar" aria-label={`${label} utilisation ${pct}%`}>
                <span style={{ width: `${pct}%`, transition: 'width 600ms ease-out' }} />
              </div>
            </div>
          )
        })}
      </div>

      <div className="shelter-section">
        <div className="subhead">
          <Ambulance size={15} />
          SHELTER CAPACITY
        </div>
        {shelters.map((shelter) => {
          const pct = Math.round((shelter.current / shelter.max) * 100)
          const isFull = shelter.current === shelter.max
          return (
            <div className={`shelter-card ${capacityTone(pct)}`} key={shelter.name}>
              <div>
                <strong>
                  {shelter.name}
                  {isFull && (
                    <span
                      style={{
                        display: 'inline-block',
                        marginLeft: 8,
                        color: '#ff3b3b',
                        font: '700 10px/1 var(--font-mono)',
                        letterSpacing: '.5px',
                      }}
                    >
                      FULL
                    </span>
                  )}
                </strong>
                <span>{shelter.current.toLocaleString('en-IN')} / {shelter.max.toLocaleString('en-IN')}</span>
              </div>
              <div
                className="capacity-meter"
                style={{
                  ['--capacity' as string]: `${pct * 3.6}deg`,
                  transition: 'background 600ms ease-out',
                }}
              >
                <span>{pct}%</span>
              </div>
            </div>
          )
        })}
      </div>
    </section>
  )
}
