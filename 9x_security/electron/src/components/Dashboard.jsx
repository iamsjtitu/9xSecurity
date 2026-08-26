import React, { useState } from 'react';
import CameraPanel from './CameraPanel.jsx';
import StatCards from './StatCards.jsx';
import ControlsPanel from './ControlsPanel.jsx';
import EventsTable from './EventsTable.jsx';

export default function Dashboard({ state, refreshState, showToast }) {
  const [drawMode, setDrawMode] = useState(false);

  return (
    <div className="grid grid-cols-1 lg:grid-cols-12 gap-6" data-testid="dashboard">
      <div className="lg:col-span-8">
        <CameraPanel
          state={state}
          refreshState={refreshState}
          showToast={showToast}
          drawMode={drawMode}
          setDrawMode={setDrawMode}
        />
      </div>
      <div className="lg:col-span-4 flex flex-col gap-6">
        <StatCards />
        <ControlsPanel
          state={state}
          refreshState={refreshState}
          showToast={showToast}
          drawMode={drawMode}
          setDrawMode={setDrawMode}
        />
      </div>
      <div className="lg:col-span-12">
        <EventsTable connected={state.connected} />
      </div>
    </div>
  );
}
