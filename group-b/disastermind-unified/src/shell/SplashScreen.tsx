type SplashScreenProps = {
  visible: boolean
}

export function SplashScreen({ visible }: SplashScreenProps) {
  return (
    <div className={`splash-screen ${visible ? 'visible' : 'hidden'}`} aria-hidden={!visible}>
      <div className="splash-copy">
        <strong>DISASTERMIND</strong>
        <span>AI-POWERED DISASTER RESPONSE COORDINATION</span>
        <div className="splash-progress" aria-hidden="true">
          <span />
        </div>
        <p>INITIALISING SYSTEM...</p>
      </div>
    </div>
  )
}
