let python;
let initialization;
async function boot() {
  importScripts('./vendor/pyodide/pyodide.js');
  const root = new URL('./vendor/pyodide/', self.location.href).href;
  python = await loadPyodide({ indexURL: root });
  for (const name of ['bond_engine.py', 'workflow.py']) {
    const response = await fetch('./' + name);
    if (!response.ok) throw new Error('Could not load Python source: ' + name);
    python.FS.writeFile('/home/pyodide/' + name, await response.text());
  }
  await python.runPythonAsync('from workflow import run_json');
}
self.onmessage = async ({data}) => {
  try {
    if (!initialization) initialization = boot();
    await initialization;
    if (data.type === 'init') { self.postMessage({id:data.id, ready:true}); return; }
    python.globals.set('input_json', JSON.stringify(data.rows));
    const result = await python.runPythonAsync('run_json(input_json)');
    self.postMessage({id:data.id, result:JSON.parse(result)});
  } catch (error) {
    if (!python) initialization = undefined;
    self.postMessage({id:data.id, error:String(error.message || error)});
  }
};
