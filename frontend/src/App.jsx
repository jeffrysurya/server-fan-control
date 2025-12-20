import { useState, useEffect, useCallback } from 'react'
import { LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer, ReferenceDot } from 'recharts'

// Icons as simple SVG components
const FanIcon = ({ rpm, className }) => {
  const animationDuration = rpm > 0 ? Math.max(0.1, 2 - (rpm / 1000)) : 0
  return (
    <svg 
      className={className} 
      viewBox="0 0 24 24" 
      fill="currentColor"
      style={rpm > 0 ? { animation: `spin ${animationDuration}s linear infinite` } : {}}
    >
      <path d="M12 11a1 1 0 1 0 0 2 1 1 0 0 0 0-2zm-1-9C6.1 2 2 5 2 9c0 2.4 1.2 4.5 3 5.7V15c0 .6.4 1 1 1h2c.6 0 1-.4 1-1v-.3c1 .2 2 .3 3 .3s2-.1 3-.3v.3c0 .6.4 1 1 1h2c.6 0 1-.4 1-1v-.3c1.8-1.2 3-3.3 3-5.7 0-4-4.1-7-9-7zm0 12c-4 0-7-2.2-7-5s3-5 7-5 7 2.2 7 5-3 5-7 5z"/>
    </svg>
  )
}

const ThermometerIcon = ({ className }) => (
  <svg className={className} viewBox="0 0 24 24" fill="currentColor">
    <path d="M12 2a3 3 0 0 0-3 3v8.26a5 5 0 1 0 6 0V5a3 3 0 0 0-3-3zm0 18a3 3 0 1 1 0-6 3 3 0 0 1 0 6z"/>
  </svg>
)

// Fan Card Component
function FanCard({ fan, fanName, onNameChange, onPWMModeChange, isManual, currentTemp }) {
  const [editing, setEditing] = useState(false)
  const [name, setName] = useState(fanName)
  
  const handleSave = () => {
    onNameChange(fan.id, name)
    setEditing(false)
  }

  const modeLabel = {
    0: 'Off',
    1: 'Manual',
    2: 'Thermal Cruise',
    5: 'Auto (BIOS)',
  }[fan.mode] || `Mode ${fan.mode}`
  
  const pwmModeLabel = fan.pwm_mode === 0 ? 'DC' : 'PWM'

  return (
    <div className="fan-card">
      <div className="flex items-center justify-between mb-3">
        <div className="flex items-center gap-2">
          <FanIcon rpm={fan.rpm} className="w-8 h-8 text-blue-400" />
          {editing ? (
            <input
              type="text"
              value={name}
              onChange={(e) => setName(e.target.value)}
              onBlur={handleSave}
              onKeyDown={(e) => e.key === 'Enter' && handleSave()}
              className="bg-gray-700 px-2 py-1 rounded text-sm"
              autoFocus
            />
          ) : (
            <span 
              className="font-medium cursor-pointer hover:text-blue-400"
              onClick={() => setEditing(true)}
              title="Click to rename"
            >
              {fanName}
            </span>
          )}
        </div>
        <div className="flex gap-2">
          <span className={`text-xs px-2 py-1 rounded ${isManual ? 'bg-orange-600' : 'bg-green-600'}`}>
            {modeLabel}
          </span>
          <button
            onClick={() => onPWMModeChange(fan.id, fan.pwm_mode === 0 ? 1 : 0)}
            className={`text-xs px-2 py-1 rounded transition-colors ${
              fan.pwm_mode === 1 ? 'bg-blue-600 hover:bg-blue-500' : 'bg-purple-600 hover:bg-purple-500'
            }`}
            title="Click to toggle between DC and PWM mode"
          >
            {pwmModeLabel}
          </button>
        </div>
      </div>
      
      <div className="grid grid-cols-2 gap-4 text-sm">
        <div>
          <div className="text-gray-400">RPM</div>
          <div className="text-2xl font-bold text-blue-300">{fan.rpm}</div>
        </div>
        <div>
          <div className="text-gray-400">PWM</div>
          <div className="text-2xl font-bold text-green-300">{fan.pwm_percent}%</div>
        </div>
      </div>
      
      {/* Mini curve preview */}
      <div className="mt-3 h-16 bg-gray-900 rounded overflow-hidden">
        <ResponsiveContainer width="100%" height="100%">
          <LineChart data={fan.curve} margin={{ top: 5, right: 5, bottom: 5, left: 5 }}>
            <Line 
              type="monotone" 
              dataKey="pwm_percent" 
              stroke="#60a5fa" 
              strokeWidth={2}
              dot={false}
            />
            {currentTemp && (
              <ReferenceDot
                x={fan.curve.findIndex(p => p.temp >= currentTemp) || 0}
                y={fan.pwm_percent}
                r={4}
                fill="#ef4444"
                stroke="none"
              />
            )}
          </LineChart>
        </ResponsiveContainer>
      </div>
    </div>
  )
}

// Curve Editor Component with Drag-and-Drop
function CurveEditor({ fanId, fanName, curve, onSave, onClose }) {
  const [points, setPoints] = useState(
    curve.map(p => ({ ...p, pwm_percent: Math.round(p.pwm / 255 * 100) }))
  )
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState(null)
  const [dragging, setDragging] = useState(null) // { index, startX, startY }
  const [chartDimensions, setChartDimensions] = useState({ width: 0, height: 0, left: 0, top: 0 })
  const chartRef = useCallback(node => {
    if (node) {
      const rect = node.getBoundingClientRect()
      setChartDimensions({
        width: rect.width - 40, // account for margins
        height: rect.height - 30,
        left: rect.left + 10,
        top: rect.top + 10
      })
    }
  }, [])

  const updatePoint = (index, field, value) => {
    const newPoints = [...points]
    const numValue = parseInt(value) || 0
    
    if (field === 'temp') {
      newPoints[index].temp = Math.max(0, Math.min(100, numValue))
    } else if (field === 'pwm_percent') {
      const percent = Math.max(0, Math.min(100, numValue))
      newPoints[index].pwm_percent = percent
      newPoints[index].pwm = Math.round(percent * 255 / 100)
    }
    
    setPoints(newPoints)
    setError(null)
  }

  const updatePointFromDrag = (index, temp, pwm) => {
    const newPoints = [...points]
    
    // Apply constraints based on neighboring points
    const prevPoint = index > 0 ? newPoints[index - 1] : null
    const nextPoint = index < newPoints.length - 1 ? newPoints[index + 1] : null
    
    // Constrain temperature
    let constrainedTemp = Math.max(0, Math.min(100, temp))
    if (prevPoint) constrainedTemp = Math.max(prevPoint.temp, constrainedTemp)
    if (nextPoint) constrainedTemp = Math.min(nextPoint.temp, constrainedTemp)
    
    // Constrain PWM
    let constrainedPwm = Math.max(0, Math.min(100, pwm))
    if (prevPoint) constrainedPwm = Math.max(prevPoint.pwm_percent, constrainedPwm)
    if (nextPoint) constrainedPwm = Math.min(nextPoint.pwm_percent, constrainedPwm)
    
    newPoints[index].temp = Math.round(constrainedTemp)
    newPoints[index].pwm_percent = Math.round(constrainedPwm)
    newPoints[index].pwm = Math.round(constrainedPwm * 255 / 100)
    
    setPoints(newPoints)
    setError(null)
  }

  const handleMouseDown = (index) => (e) => {
    e.preventDefault()
    setDragging({ index, startX: e.clientX, startY: e.clientY })
  }

  const handleMouseMove = useCallback((e) => {
    if (!dragging || !chartDimensions.width) return
    
    const { index } = dragging
    const { left, top, width, height } = chartDimensions
    
    // Calculate position relative to chart
    const x = e.clientX - left
    const y = e.clientY - top
    
    // Convert pixel position to data values
    const temp = (x / width) * 100
    const pwm = 100 - (y / height) * 100 // Invert Y axis
    
    updatePointFromDrag(index, temp, pwm)
  }, [dragging, chartDimensions])

  const handleMouseUp = useCallback(() => {
    setDragging(null)
  }, [])

  useEffect(() => {
    if (dragging) {
      window.addEventListener('mousemove', handleMouseMove)
      window.addEventListener('mouseup', handleMouseUp)
      return () => {
        window.removeEventListener('mousemove', handleMouseMove)
        window.removeEventListener('mouseup', handleMouseUp)
      }
    }
  }, [dragging, handleMouseMove, handleMouseUp])

  const validateCurve = () => {
    for (let i = 1; i < points.length; i++) {
      if (points[i].temp < points[i-1].temp) {
        return 'Temperatures must be increasing'
      }
      if (points[i].pwm < points[i-1].pwm) {
        return 'PWM values must be increasing'
      }
    }
    return null
  }

  const handleSave = async () => {
    const err = validateCurve()
    if (err) {
      setError(err)
      return
    }
    
    setSaving(true)
    try {
      await onSave(fanId, points.map(p => ({
        point: p.point,
        temp: p.temp,
        pwm: p.pwm
      })))
      onClose()
    } catch (e) {
      setError(e.message)
    } finally {
      setSaving(false)
    }
  }

  const chartData = points.map(p => ({
    temp: p.temp,
    pwm: p.pwm_percent
  }))

  return (
    <div className="fixed inset-0 bg-black/70 flex items-center justify-center z-50 p-4">
      <div className="bg-gray-800 rounded-lg p-6 w-full max-w-2xl max-h-[90vh] overflow-auto">
        <div className="flex justify-between items-center mb-4">
          <h2 className="text-xl font-bold">Edit Curve: {fanName}</h2>
          <button onClick={onClose} className="text-gray-400 hover:text-white text-2xl">&times;</button>
        </div>
        
        <div className="mb-2 text-sm text-gray-400">
          💡 Drag points on the chart or use input fields below for precise control
        </div>
        
        {/* Chart */}
        <div 
          ref={chartRef}
          className="h-64 mb-6 bg-gray-900 rounded p-2 select-none"
          style={{ cursor: dragging ? 'grabbing' : 'default' }}
        >
          <ResponsiveContainer width="100%" height="100%">
            <LineChart data={chartData} margin={{ top: 10, right: 30, bottom: 20, left: 10 }}>
              <CartesianGrid strokeDasharray="3 3" stroke="#374151" />
              <XAxis 
                dataKey="temp" 
                label={{ value: 'Temperature (°C)', position: 'bottom', fill: '#9ca3af' }}
                stroke="#9ca3af"
                domain={[0, 100]}
              />
              <YAxis 
                label={{ value: 'PWM %', angle: -90, position: 'insideLeft', fill: '#9ca3af' }}
                stroke="#9ca3af"
                domain={[0, 100]}
              />
              <Tooltip 
                contentStyle={{ backgroundColor: '#1f2937', border: '1px solid #374151' }}
                labelFormatter={(v) => `${v}°C`}
                formatter={(v) => [`${v}%`, 'PWM']}
              />
              <Line 
                type="monotone" 
                dataKey="pwm" 
                stroke="#60a5fa" 
                strokeWidth={3}
                dot={false}
              />
              {/* Custom draggable dots */}
              {points.map((point, idx) => {
                const x = (point.temp / 100) * (chartDimensions.width || 1)
                const y = ((100 - point.pwm_percent) / 100) * (chartDimensions.height || 1)
                const isDragging = dragging?.index === idx
                
                return (
                  <g key={point.point}>
                    <circle
                      cx={x + 10}
                      cy={y + 10}
                      r={isDragging ? 10 : 8}
                      fill={isDragging ? '#3b82f6' : '#60a5fa'}
                      stroke="#1f2937"
                      strokeWidth={2}
                      style={{ cursor: 'grab' }}
                      onMouseDown={handleMouseDown(idx)}
                    />
                    {isDragging && (
                      <text
                        x={x + 10}
                        y={y - 5}
                        textAnchor="middle"
                        fill="#60a5fa"
                        fontSize="12"
                        fontWeight="bold"
                      >
                        {point.temp}°C, {point.pwm_percent}%
                      </text>
                    )}
                  </g>
                )
              })}
            </LineChart>
          </ResponsiveContainer>
        </div>
        
        {/* Point editors */}
        <div className="space-y-3">
          <div className="grid grid-cols-3 gap-4 text-sm text-gray-400 font-medium">
            <div>Point</div>
            <div>Temperature (°C)</div>
            <div>PWM (%)</div>
          </div>
          
          {points.map((point, idx) => (
            <div key={point.point} className="grid grid-cols-3 gap-4 items-center">
              <div className="text-gray-300">Point {point.point}</div>
              <input
                type="number"
                value={point.temp}
                onChange={(e) => updatePoint(idx, 'temp', e.target.value)}
                className="bg-gray-700 rounded px-3 py-2 w-full"
                min="0"
                max="100"
              />
              <input
                type="number"
                value={point.pwm_percent}
                onChange={(e) => updatePoint(idx, 'pwm_percent', e.target.value)}
                className="bg-gray-700 rounded px-3 py-2 w-full"
                min="0"
                max="100"
              />
            </div>
          ))}
        </div>
        
        {error && (
          <div className="mt-4 p-3 bg-red-900/50 border border-red-700 rounded text-red-300">
            {error}
          </div>
        )}
        
        <div className="mt-6 flex justify-end gap-3">
          <button 
            onClick={onClose}
            className="px-4 py-2 bg-gray-700 hover:bg-gray-600 rounded"
          >
            Cancel
          </button>
          <button 
            onClick={handleSave}
            disabled={saving}
            className="px-4 py-2 bg-blue-600 hover:bg-blue-500 rounded disabled:opacity-50"
          >
            {saving ? 'Saving...' : 'Save Curve'}
          </button>
        </div>
      </div>
    </div>
  )
}

// Temperature Display
function TemperatureDisplay({ temps }) {
  const tempEntries = Object.entries(temps).filter(([_, v]) => v > 0 && v < 100)
  
  return (
    <div className="fan-card">
      <h2 className="text-lg font-bold mb-3 flex items-center gap-2">
        <ThermometerIcon className="w-5 h-5 text-red-400" />
        Temperatures
      </h2>
      <div className="grid grid-cols-2 md:grid-cols-3 gap-3">
        {tempEntries.map(([name, value]) => (
          <div key={name} className="bg-gray-900 rounded p-2">
            <div className="text-xs text-gray-400 truncate">{name}</div>
            <div className={`text-lg font-bold ${value > 70 ? 'text-red-400' : value > 50 ? 'text-yellow-400' : 'text-green-400'}`}>
              {value.toFixed(1)}°C
            </div>
          </div>
        ))}
      </div>
    </div>
  )
}

// Main App
export default function App() {
  const [status, setStatus] = useState(null)
  const [config, setConfig] = useState(null)
  const [mode, setMode] = useState('auto')
  const [editingFan, setEditingFan] = useState(null)
  const [switching, setSwitching] = useState(false)
  const [connected, setConnected] = useState(false)
  const [error, setError] = useState(null)

  // WebSocket connection
  useEffect(() => {
    let ws = null
    let reconnectTimeout = null
    
    const connect = () => {
      const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
      ws = new WebSocket(`${protocol}//${window.location.host}/ws`)
      
      ws.onopen = () => {
        setConnected(true)
        setError(null)
      }
      
      ws.onmessage = (event) => {
        try {
          const data = JSON.parse(event.data)
          setStatus(data)
          if (data.config_mode) {
            setMode(data.config_mode)
          }
        } catch (e) {
          console.error('Failed to parse status:', e)
        }
      }
      
      ws.onclose = () => {
        setConnected(false)
        reconnectTimeout = setTimeout(connect, 2000)
      }
      
      ws.onerror = () => {
        setError('WebSocket error')
      }
    }
    
    connect()
    
    return () => {
      if (ws) ws.close()
      if (reconnectTimeout) clearTimeout(reconnectTimeout)
    }
  }, [])

  // Load initial config
  useEffect(() => {
    fetch('/api/config')
      .then(r => r.json())
      .then(setConfig)
      .catch(e => setError('Failed to load config'))
  }, [])

  const handleModeChange = async (newMode) => {
    setSwitching(true)
    try {
      const res = await fetch('/api/mode', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ mode: newMode })
      })
      if (!res.ok) throw new Error('Failed to change mode')
      setMode(newMode)
    } catch (e) {
      setError(e.message)
    } finally {
      setSwitching(false)
    }
  }

  const handleSaveCurve = async (fanId, curve) => {
    const res = await fetch('/api/curve', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ fan_id: fanId, curve })
    })
    if (!res.ok) {
      const data = await res.json()
      throw new Error(data.detail || 'Failed to save curve')
    }
    
    // Update local config
    setConfig(prev => ({
      ...prev,
      curves: { ...prev.curves, [fanId]: curve }
    }))
  }

  const handleNameChange = async (fanId, name) => {
    try {
      await fetch('/api/fan_name', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ fan_id: fanId, name })
      })
      setConfig(prev => ({
        ...prev,
        fan_names: { ...prev.fan_names, [fanId]: name }
      }))
    } catch (e) {
      console.error('Failed to save name:', e)
    }
  }

  const handlePWMModeChange = async (fanId, pwmMode) => {
    try {
      const res = await fetch('/api/pwm_mode', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ fan_id: fanId, pwm_mode: pwmMode })
      })
      if (!res.ok) {
        const data = await res.json()
        throw new Error(data.detail || 'Failed to set PWM mode')
      }
      // Config will be updated via WebSocket status update
    } catch (e) {
      setError(e.message)
      console.error('Failed to set PWM mode:', e)
    }
  }

  const fanNames = config?.fan_names || {}
  const curves = config?.curves || {}
  const cpuTemp = status?.temps?.['CPU (Tctl)'] || status?.temps?.CPUTIN

  return (
    <div className="min-h-screen p-4 md:p-6">
      <style>{`
        @keyframes spin {
          from { transform: rotate(0deg); }
          to { transform: rotate(360deg); }
        }
      `}</style>
      
      {/* Header */}
      <div className="max-w-6xl mx-auto">
        <div className="flex flex-col md:flex-row md:items-center justify-between gap-4 mb-6">
          <div>
            <h1 className="text-2xl font-bold flex items-center gap-2">
              🌀 Fan Control
            </h1>
            <p className="text-gray-400 text-sm mt-1">
              {connected ? (
                <span className="text-green-400">● Connected</span>
              ) : (
                <span className="text-red-400">● Disconnected</span>
              )}
              {status?.hwmon && <span className="ml-2">• {status.hwmon}</span>}
            </p>
          </div>
          
          {/* Mode Toggle */}
          <div className="flex items-center gap-3 bg-gray-800 rounded-lg p-1">
            <button
              onClick={() => handleModeChange('auto')}
              disabled={switching}
              className={`px-4 py-2 rounded-md transition-colors ${
                mode === 'auto' 
                  ? 'bg-green-600 text-white' 
                  : 'text-gray-400 hover:text-white'
              }`}
            >
              Auto (BIOS)
            </button>
            <button
              onClick={() => handleModeChange('manual')}
              disabled={switching}
              className={`px-4 py-2 rounded-md transition-colors ${
                mode === 'manual' 
                  ? 'bg-orange-600 text-white' 
                  : 'text-gray-400 hover:text-white'
              }`}
            >
              Manual Curve
            </button>
          </div>
        </div>
        
        {error && (
          <div className="mb-4 p-3 bg-red-900/50 border border-red-700 rounded text-red-300">
            {error}
            <button onClick={() => setError(null)} className="ml-2 text-red-400 hover:text-white">&times;</button>
          </div>
        )}
        
        {/* Temperatures */}
        {status?.temps && <TemperatureDisplay temps={status.temps} />}
        
        {/* Fan Grid */}
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4 mt-4">
          {status?.fans?.map((fan) => (
            <div key={fan.id} className="relative">
              <FanCard
                fan={fan}
                fanName={fanNames[fan.id] || `Fan ${fan.id}`}
                onNameChange={handleNameChange}
                onPWMModeChange={handlePWMModeChange}
                isManual={mode === 'manual'}
                currentTemp={cpuTemp}
              />
              <button
                onClick={() => setEditingFan(fan.id)}
                className="absolute top-2 right-2 p-2 text-gray-400 hover:text-white hover:bg-gray-700 rounded"
                title="Edit curve"
              >
                <svg className="w-4 h-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                  <path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7" />
                  <path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z" />
                </svg>
              </button>
            </div>
          ))}
        </div>
        
        {/* Help text */}
        <div className="mt-6 text-sm text-gray-500">
          <p><strong>Auto (BIOS):</strong> Fans controlled by motherboard SmartFan IV curves</p>
          <p><strong>Manual Curve:</strong> Custom temperature-based curves applied via OS. Click the edit icon on each fan to customize.</p>
          <p><strong>PWM Mode:</strong> Toggle between DC (voltage-based) and PWM (pulse width modulation) control. Click the mode badge to switch.</p>
        </div>
      </div>
      
      {/* Curve Editor Modal */}
      {editingFan && config && (
        <CurveEditor
          fanId={editingFan}
          fanName={fanNames[editingFan] || `Fan ${editingFan}`}
          curve={curves[editingFan] || status?.fans?.find(f => f.id === editingFan)?.curve || []}
          onSave={handleSaveCurve}
          onClose={() => setEditingFan(null)}
        />
      )}
    </div>
  )
}
