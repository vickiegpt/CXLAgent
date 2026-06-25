/*++
Copyright (c) 2025 CXLAgent Project

Module Name:
    cxl_etw.h

Abstract:
    ETW (Event Tracing for Windows) provider implementation for CXL drivers.

    This header provides the ETW provider registration, event logging,
    and tracepoint definitions for CXL kernel drivers.

Environment:
    Kernel mode

Usage:
    In your driver:
        #include "cxl_etw.h"

    In DriverEntry:
        CxlEtwRegisterProvider();

    In driver unload:
        CxlEtwUnregisterProvider();

    To log events:
        CxlEtwLogGeneralMediaEvent(&event);
        CxlEtwLogDramEvent(&event);
        CxlEtwLogPoisonEvent(&event);
--*/

#ifndef _CXL_ETW_H_
#define _CXL_ETW_H_

#include <ntddk.h>
#include <evntrace.h>
#include <wmistr.h>

//=============================================================================
// ETW Provider GUID
//=============================================================================

// {E8F3A5B1-2C4D-4E8F-9A1B-6C5D7E8F9A0B}
DEFINE_GUID(GUID_CXL_ETW_PROVIDER,
    0xe8f3a5b1, 0x2c4d, 0x4e8f, 0x9a, 0x1b, 0x6c, 0x5d, 0x7e, 0x8f, 0x9a, 0x0b);

//=============================================================================
// Event IDs
//=============================================================================

#define CXL_EVENT_GENERAL_MEDIA     1
#define CXL_EVENT_DRAM               2
#define CXL_EVENT_POISON             3
#define CXL_EVENT_CACHE_FLUSH        4
#define CXL_EVENT_CACHE_STATE        5
#define CXL_EVENT_MEMORY_WINDOW      6
#define CXL_EVENT_ACCEL_WORK         7

//=============================================================================
// Event Levels
//=============================================================================

#define CXL_TRACE_LEVEL_NONE         0   // Tracing is not on
#define CXL_TRACE_LEVEL_CRITICAL      1   // Abnormal exit or termination
#define CXL_TRACE_LEVEL_FATAL         1   // Deprecated name for CRITICAL
#define CXL_TRACE_LEVEL_ERROR         2   // Severe problems
#define CXL_TRACE_LEVEL_WARNING       3   // Warnings such as allocation failure
#define CXL_TRACE_LEVEL_INFO          4   // Includes non-error cases
#define CXL_TRACE_LEVEL_VERBOSE        5   // Detailed traces

//=============================================================================
// ETW Provider Handle
//=============================================================================

extern TRACE_REGISTRATION_HANDLE g_CxlEtwRegHandle;
extern ULONG g_CxlEtwEnableLevel;
extern ULONGLONG g_CxlEtwEnableFlags;

//=============================================================================
// CXL Event Data Structures
//=============================================================================

//
// CXL General Media Event
// Logged when a CXL memory transaction occurs
//
typedef struct _CXL_GENERAL_MEDIA_EVENT {
    UINT64 Timestamp;               // Event timestamp
    UINT32 CpuNumber;              // CPU number
    UINT32 ProcessId;              // Process ID
    WCHAR MemoryDevice[32];        // Memory device name (e.g., "mem0")
    UINT32 SerialNumber;           // Device serial number
    UINT32 TransactionType;        // 0=Read, 1=Write, 2=Invalidate
    UINT64 Dpa;                    // Device Physical Address
    UINT64 Hpa;                    // Host Physical Address
    UINT32 DataLength;             // Length of data transferred
    UINT32 Latency;               // Transaction latency (ns)
} CXL_GENERAL_MEDIA_EVENT, *PCXL_GENERAL_MEDIA_EVENT;

//
// CXL DRAM Event
// Logged for DRAM-specific events
//
typedef struct _CXL_DRAM_EVENT {
    UINT64 Timestamp;
    UINT32 CpuNumber;
    WCHAR MemoryDevice[32];
    UINT32 SerialNumber;
    UINT64 PhysicalAddress;
    UINT32 EventType;              // 0=Read, 1=Write, 2=Refresh
    UINT32 BankNumber;
    UINT32 RowNumber;
    UINT32 ColumnNumber;
} CXL_DRAM_EVENT, *PCXL_DRAM_EVENT;

//
// CXL Poison Event
// Logged when memory poison is detected
//
typedef struct _CXL_POISON_EVENT {
    UINT64 Timestamp;
    UINT32 CpuNumber;
    WCHAR MemoryDevice[32];
    UINT32 SerialNumber;
    UINT64 PoisonAddress;          // Address of poisoned memory
    UINT32 PoisonType;             // Type of poison
    UINT64 Pattern;                // Poison pattern (if applicable)
} CXL_POISON_EVENT, *PCXL_POISON_EVENT;

//
// CXL Cache Flush Event
// Logged when cache flush (WBINVD) is triggered
//
typedef struct _CXL_CACHE_FLUSH_EVENT {
    UINT64 Timestamp;
    UINT32 CpuNumber;
    WCHAR CacheDevice[32];         // Cache device name (e.g., "cache0")
    UINT32 FlushType;              // 0=WBINVD, 1=Writeback, 2=Invalidate
    UINT64 LinesFlushed;           // Number of cache lines flushed
    UINT32 FlushDuration;          // Duration in microseconds
} CXL_CACHE_FLUSH_EVENT, *PCXL_CACHE_FLUSH_EVENT;

//
// CXL Memory Window Event
// Logged when memory window is accessed
//
typedef struct _CXL_MEMORY_WINDOW_EVENT {
    UINT64 Timestamp;
    UINT32 CpuNumber;
    WCHAR MemoryDevice[32];
    UINT32 WindowIndex;
    UINT64 StartAddress;
    UINT64 EndAddress;
    UINT32 AccessType;             // 0=Read, 1=Write, 2=Map, 3=Unmap
} CXL_MEMORY_WINDOW_EVENT, *PCXL_MEMORY_WINDOW_EVENT;

//=============================================================================
// ETW Provider Registration
//=============================================================================

NTSTATUS
CxlEtwRegisterProvider(
    VOID
);

/*++
Routine Description:
    Registers the CXL ETW provider with the system.

Arguments:
    None

Return Value:
    STATUS_SUCCESS if successful, error code otherwise
--*/

VOID
CxlEtwUnregisterProvider(
    VOID
);

/*++
Routine Description:
    Unregisters the CXL ETW provider.

Arguments:
    None

Return Value:
    None
--*/

//=============================================================================
// ETW Event Logging Functions
//=============================================================================

VOID
CxlEtwLogGeneralMediaEvent(
    IN PCXL_GENERAL_MEDIA_EVENT Event
);

/*++
Routine Description:
    Logs a CXL general media event.

Arguments:
    Event - Pointer to the event structure

Return Value:
    None
--*/

VOID
CxlEtwLogDramEvent(
    IN PCXL_DRAM_EVENT Event
);

/*++
Routine Description:
    Logs a CXL DRAM event.

Arguments:
    Event - Pointer to the event structure

Return Value:
    None
--*/

VOID
CxlEtwLogPoisonEvent(
    IN PCXL_POISON_EVENT Event
);

/*++
Routine Description:
    Logs a CXL poison event.

Arguments:
    Event - Pointer to the event structure

Return Value:
    None
--*/

VOID
CxlEtwLogCacheFlushEvent(
    IN PCXL_CACHE_FLUSH_EVENT Event
);

/*++
Routine Description:
    Logs a CXL cache flush event.

Arguments:
    Event - Pointer to the event structure

Return Value:
    None
--*/

VOID
CxlEtwLogMemoryWindowEvent(
    IN PCXL_MEMORY_WINDOW_EVENT Event
);

/*++
Routine Description:
    Logs a CXL memory window event.

Arguments:
    Event - Pointer to the event structure

Return Value:
    None
--*/

//=============================================================================
// ETW Enable/Disable Callbacks
//=============================================================================

VOID
NTAPI
CxlEtwEnableCallback(
    IN LPCGUID SourceId,
    IN ULONG IsEnabled,
    IN UCHAR Level,
    IN ULONGLONG MatchAnyKeyword,
    IN ULONGLONG MatchAllKeyword,
    IN PVOID FilterData,
    IN OUT PVOID CallbackContext
);

/*++
Routine Description:
    Callback function called when ETW session enables/disables the provider.

Arguments:
    SourceId - GUID of the trace session
    IsEnabled - TRUE if enabling, FALSE if disabling
    Level - Trace level
    MatchAnyKeyword - Match any keyword flag
    MatchAllKeyword - Match all keyword flag
    FilterData - Optional filter data
    CallbackContext - Context pointer

Return Value:
    None
--*/

//=============================================================================
// Helper Macros
//=============================================================================

//
// Check if tracing is enabled
//
#define CXL_ETW_IS_ENABLED() \
    (g_CxlEtwEnableLevel > CXL_TRACE_LEVEL_NONE)

//
// Check if specific level is enabled
//
#define CXL_ETW_IS_LEVEL_ENABLED(Level) \
    (g_CxlEtwEnableLevel >= Level)

//
// Check if specific keyword is enabled
//
#define CXL_ETW_IS_KEYWORD_ENABLED(Keyword) \
    (g_CxlEtwEnableFlags & (Keyword))

//
// Get current timestamp
//
#define CXL_ETW_GET_TIMESTAMP() \
    KeQueryPerformanceCounter(NULL).QuadPart

//
// Log event if tracing is enabled (with level check)
//
#define CXL_ETW_LOG_IF_LEVEL(Level, Function) \
    if (CXL_ETW_IS_LEVEL_ENABLED(Level)) { \
        Function; \
    }

//=============================================================================
// Convenience Logging Macros
//=============================================================================

#define CXL_ETW_LOG_INFO(Message) \
    if (CXL_ETW_IS_LEVEL_ENABLED(CXL_TRACE_LEVEL_INFO)) { \
        KdPrint(("CXL_ETW: %s\n", Message)); \
    }

#define CXL_ETW_LOG_WARNING(Message) \
    if (CXL_ETW_IS_LEVEL_ENABLED(CXL_TRACE_LEVEL_WARNING)) { \
        KdPrint(("CXL_ETW WARNING: %s\n", Message)); \
    }

#define CXL_ETW_LOG_ERROR(Message) \
    if (CXL_ETW_IS_LEVEL_ENABLED(CXL_TRACE_LEVEL_ERROR)) { \
        KdPrint(("CXL_ETW ERROR: %s\n", Message)); \
    }

#define CXL_ETW_LOG_VERBOSE(Message) \
    if (CXL_ETW_IS_LEVEL_ENABLED(CXL_TRACE_LEVEL_VERBOSE)) { \
        KdPrint(("CXL_ETW VERBOSE: %s\n", Message)); \
    }

//=============================================================================
// Inline Implementation (for header-only use)
//=============================================================================

#ifdef CXL_ETW_INLINE_IMPLEMENTATION

TRACE_REGISTRATION_HANDLE g_CxlEtwRegHandle = 0;
ULONG g_CxlEtwEnableLevel = CXL_TRACE_LEVEL_NONE;
ULONGLONG g_CxlEtwEnableFlags = 0;

//
// Event descriptors (for internal use)
//
EXTERN_C const EVENT_DESCRIPTOR CxlGeneralMediaEventDesc;
EXTERN_C const EVENT_DESCRIPTOR CxlDramEventDesc;
EXTERN_C const EVENT_DESCRIPTOR CxlPoisonEventDesc;

//
// Enable Callback Implementation
//

VOID
NTAPI
CxlEtwEnableCallback(
    IN LPCGUID SourceId,
    IN ULONG IsEnabled,
    IN UCHAR Level,
    IN ULONGLONG MatchAnyKeyword,
    IN ULONGLONG MatchAllKeyword,
    IN PVOID FilterData,
    IN OUT PVOID CallbackContext
)
{
    UNREFERENCED_PARAMETER(SourceId);
    UNREFERENCED_PARAMETER(MatchAnyKeyword);
    UNREFERENCED_PARAMETER(MatchAllKeyword);
    UNREFERENCED_PARAMETER(FilterData);
    UNREFERENCED_PARAMETER(CallbackContext);

    if (IsEnabled) {
        g_CxlEtwEnableLevel = Level;
        g_CxlEtwEnableFlags = MatchAnyKeyword;
    } else {
        g_CxlEtwEnableLevel = CXL_TRACE_LEVEL_NONE;
        g_CxlEtwEnableFlags = 0;
    }
}

//
// Provider Registration Implementation
//

NTSTATUS
CxlEtwRegisterProvider(
    VOID
)
{
    NTSTATUS status;

    if (g_CxlEtwRegHandle != 0) {
        return STATUS_SUCCESS;  // Already registered
    }

    status = EtwRegister(
        &GUID_CXL_ETW_PROVIDER,
        CxlEtwEnableCallback,
        NULL,
        &g_CxlEtwRegHandle
    );

    if (NT_SUCCESS(status)) {
        KdPrint(("CXL_ETW: Provider registered successfully\n"));
    } else {
        KdPrint(("CXL_ETW: Failed to register provider: 0x%x\n", status));
    }

    return status;
}

VOID
CxlEtwUnregisterProvider(
    VOID
)
{
    if (g_CxlEtwRegHandle != 0) {
        EtwUnregister(g_CxlEtwRegHandle);
        g_CxlEtwRegHandle = 0;
        g_CxlEtwEnableLevel = CXL_TRACE_LEVEL_NONE;
        g_CxlEtwEnableFlags = 0;

        KdPrint(("CXL_ETW: Provider unregistered\n"));
    }
}

#endif // CXL_ETW_INLINE_IMPLEMENTATION

#endif // _CXL_ETW_H_
