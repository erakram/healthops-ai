import { NextResponse, type NextRequest } from "next/server";

// Only checks that a session cookie exists, to send signed-out visitors to /login.
// The backend verifies the token and the role on every API call.
export function middleware(request: NextRequest) {
  const signedIn = request.cookies.has("healthops_session");
  const { pathname } = request.nextUrl;
  if (!signedIn && pathname !== "/login") {
    const url = new URL("/login", request.url);
    if (pathname !== "/") url.searchParams.set("next", pathname);
    return NextResponse.redirect(url);
  }
  if (signedIn && (pathname === "/login" || pathname === "/")) {
    return NextResponse.redirect(new URL("/dashboard", request.url));
  }
  return NextResponse.next();
}

export const config = {
  matcher: ["/((?!api|_next|favicon.ico|.*\\..*).*)"],
};
