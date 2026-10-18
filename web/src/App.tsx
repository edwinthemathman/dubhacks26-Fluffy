import { Route, Routes } from "react-router-dom";
import { FluffyStreamProvider } from "./stream/FluffyStream";
import { Landing } from "./pages/Landing/Landing";
import { Mind } from "./pages/Mind/Mind";
import { Demo } from "./pages/Demo/Demo";
import { Companion } from "./pages/Companion/Companion";
import { Caregiver } from "./pages/Caregiver/Caregiver";

export function App() {
  return (
    <FluffyStreamProvider>
      <a className="app-skip" href="#main">
        Skip to content
      </a>
      <Routes>
        <Route path="/" element={<Landing />} />
        <Route path="/mind" element={<Mind />} />
        <Route path="/demo" element={<Demo />} />
        <Route path="/companion" element={<Companion />} />
        <Route path="/caregiver" element={<Caregiver />} />
      </Routes>
    </FluffyStreamProvider>
  );
}
