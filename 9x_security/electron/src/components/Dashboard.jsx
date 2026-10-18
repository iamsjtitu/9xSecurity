import React, { useState } from 'react';
import CameraPanel from './CameraPanel.jsx';
import StatCards from './StatCards.jsx';
import LoadMeter from './LoadMeter.jsx';
import ControlsPanel from './ControlsPanel.jsx';
import EventsTable from './EventsTable.jsx';
import PlateSearchPanel from './PlateSearchPanel.jsx';
import ReportRangeCard from './ReportRangeCard.jsx';
import PlateCreditsCard from './PlateCreditsCard.jsx';

export default function Dashboard({ state, refreshState, showToast, onManageCameras, onOpenSettings }) {
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
          onManageCameras={onManageCameras}
        />
      </div>
      <div className="lg:col-span-4 flex flex-col gap-6">
        <StatCards />
        <PlateCreditsCard onOpenSettings={onOpenSettings} />
        <LoadMeter state={state} />
        <ControlsPanel
          state={state}
          refreshState={refreshState}
          showToast={showToast}
          drawMode={drawMode}
          setDrawMode={setDrawMode}
        />
        <ReportRangeCard showToast={showToast} />
      </div>
      <div className="lg:col-span-12">
        <PlateSearchPanel />
      </div>
      <div className="lg:col-span-12">
        <EventsTable connected={state.connected} showToast={showToast} />
      </div>
    </div>
  );
}
