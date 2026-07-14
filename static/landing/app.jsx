/* global React, ReactDOM, EditorHero, StartHere, Foot, useReveal */
const { useEffect } = React;

function App() {
  useReveal();

  useEffect(() => {
    const b = document.body;
    b.classList.remove("theme-paper", "theme-night", "theme-contrast");
    b.classList.add("theme-paper", "no-motion", "no-grain");
  }, []);

  return (
    <React.Fragment>
      <EditorHero />
      <StartHere />
      <Foot />
    </React.Fragment>
  );
}

ReactDOM.createRoot(document.getElementById("root")).render(<App />);
