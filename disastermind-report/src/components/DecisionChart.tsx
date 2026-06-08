import { Bar, BarChart, CartesianGrid, Cell, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'

const data = [
  { agent: 'FLOOD-AI', decisions: 312, color: '#1a3a6b' },
  { agent: 'RESOURCE-AI', decisions: 241, color: '#1a7a4a' },
  { agent: 'EVAC-AI', decisions: 187, color: '#d35400' },
  { agent: 'COORD-AI', decisions: 107, color: '#c0392b' },
]

export default function DecisionChart() {
  return (
    <section className="chart-card">
      <h2>Decisions by Agent Type</h2>
      <div className="chart-frame">
        <ResponsiveContainer height={260} width="100%">
          <BarChart data={data} margin={{ top: 20, right: 16, left: 0, bottom: 10 }}>
            <CartesianGrid stroke="#d0d7de" strokeDasharray="3 3" />
            <XAxis dataKey="agent" tick={{ fill: '#5a6a7a', fontSize: 12 }} />
            <YAxis tick={{ fill: '#5a6a7a', fontSize: 12 }} />
            <Tooltip cursor={{ fill: '#eef2f5' }} />
            <Bar dataKey="decisions" label={{ position: 'top', fill: '#1a1a2e', fontSize: 12 }}>
              {data.map((entry) => (
                <Cell fill={entry.color} key={entry.agent} />
              ))}
            </Bar>
          </BarChart>
        </ResponsiveContainer>
      </div>
    </section>
  )
}
