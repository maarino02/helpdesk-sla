import { roleName, startPage } from "../layout.js";

const user = await startPage();

document.querySelector("[data-user-first-name]").textContent = user.full_name.split(" ")[0];
document.querySelector("[data-user-email]").textContent = user.email;
document.querySelector("[data-user-role]").textContent = roleName(user.role);
document.querySelector("[data-user-organization]").textContent =
  user.organization?.name ?? "Equipo de soporte";
