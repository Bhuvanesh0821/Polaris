import React from 'react'
import ReactDOM from 'react-dom/client'
import { BrowserRouter, Navigate, Route, Routes } from 'react-router-dom'

import './styles/theme.css'
import './styles/app.css'

import AppLayout from './layouts/AppLayout'
import { PolarisProvider } from './hooks/usePolaris'

import Overview from './pages/Overview'
import EnergyDashboard from './pages/EnergyDashboard'
import LiveWeather from './pages/LiveWeather'
import Alerts from './pages/Alerts'
import LoadForecast from './pages/LoadForecast'
import RenewableForecast from './pages/RenewableForecast'
import Optimization from './pages/Optimization'
import Survival from './pages/Survival'
import CrisisSimulator from './pages/CrisisSimulator'
import Explainability from './pages/Explainability'
import DataModel from './pages/DataModel'
import SettingsPage from './pages/Settings'

ReactDOM.createRoot(document.getElementById('root')).render(
  <React.StrictMode>
    <BrowserRouter>
      <PolarisProvider>
        <Routes>
          <Route path="/" element={<AppLayout />}>
            <Route index element={<Overview />} />
            <Route path="energy" element={<EnergyDashboard />} />
            <Route path="weather" element={<LiveWeather />} />
            <Route path="alerts" element={<Alerts />} />
            <Route path="load-forecast" element={<LoadForecast />} />
            <Route path="renewable-forecast" element={<RenewableForecast />} />
            <Route path="optimization" element={<Optimization />} />
            <Route path="survival" element={<Survival />} />
            <Route path="crisis" element={<CrisisSimulator />} />
            <Route path="explainability" element={<Explainability />} />
            <Route path="data-model" element={<DataModel />} />
            <Route path="settings" element={<SettingsPage />} />
            <Route path="*" element={<Navigate to="/" replace />} />
          </Route>
        </Routes>
      </PolarisProvider>
    </BrowserRouter>
  </React.StrictMode>,
)
