const { contextBridge, ipcRenderer } = require("electron");

contextBridge.exposeInMainWorld("primitiveDesktop", {
  onCommand: callback => {
    ipcRenderer.on("primitive:command", (_event, command) => {
      if (command === "new-task" || command === "search") callback(command);
    });
  },
});
