import { NavLink, Route, Routes } from "react-router-dom";
import Library from "./routes/Library";
import BookDetailPage from "./routes/BookDetail";
import Review from "./routes/Review";
import Jobs from "./routes/Jobs";
import Settings from "./routes/Settings";
import Reader from "./reader/Reader";
import JobStatusBar from "./components/JobStatusBar";

export default function App() {
  return (
    <div className="app">
      <nav className="nav">
        <span className="brand">reshelf</span>
        <NavLink to="/">Library</NavLink>
        <NavLink to="/review">Review</NavLink>
        <NavLink to="/jobs">Jobs</NavLink>
        <NavLink to="/settings">Settings</NavLink>
      </nav>
      <main>
        <Routes>
          <Route path="/" element={<Library />} />
          <Route path="/book/:sha" element={<BookDetailPage />} />
          <Route path="/read/:sha" element={<Reader />} />
          <Route path="/review" element={<Review />} />
          <Route path="/jobs" element={<Jobs />} />
          <Route path="/settings" element={<Settings />} />
        </Routes>
      </main>
      <JobStatusBar />
    </div>
  );
}
